<?php
/**
 * ZeRock — homepage "Now Broadcasting" widget renderer
 * ====================================================
 *
 * Replaces the hardcoded weekly schedule that used to live inside the WP
 * theme PHP. Reads the live source-of-truth that Rocky pushes into
 * `zerock_now_playing_json` (same data that drives the weekly grid) and
 * renders the existing `.hp-live-now` / `.hp-next-show` markup, so the
 * theme's CSS keeps working unchanged.
 *
 * INSTALLATION (two small steps via WP Admin):
 *
 * 1) Add this file's contents as a WPCode "PHP Snippet"
 *    -------------------------------------------------------------
 *    WP Admin → Code Snippets → + Add Snippet → Add Your Custom Code (PHP Snippet)
 *      Title:    ZeRock — Now Broadcasting widget
 *      Insert:   Auto Insert
 *      Location: Run Everywhere   (or "Frontend Only")
 *      Code:     paste the entire block below (without the leading <?php)
 *      Save & Activate.
 *
 * 2) Swap the theme's hardcoded widget for the shortcode
 *    -------------------------------------------------------------
 *    WP Admin → Appearance → Theme File Editor → front-page.php
 *    Find the existing block that begins with
 *        <div class="hp-live-now-title">עכשיו בשידור</div>
 *    and ends with
 *        <div class="hp-button-schedule">…<a href="/schedule/">…</a></div>
 *    Replace that ENTIRE block with one line:
 *
 *        <?php echo do_shortcode('[zerock_now_playing]'); ?>
 *
 *    Save the file. (Keep a copy of the original snippet somewhere — you
 *    can paste it back to revert in seconds.)
 *
 * 3) Clear your page cache
 *    -------------------------------------------------------------
 *    If you have a page-cache plugin (WP Rocket / W3 Total Cache /
 *    LiteSpeed / WP Super Cache / SiteGround Optimizer / etc.) clear
 *    the homepage cache so the new code starts running immediately.
 *
 *
 * HOW IT STAYS LIVE
 * -----------------
 * Rocky calls _sync_wp_board() on every schedule change and once per
 * scheduler tick. That writes `zerock_now_playing_json` (50 weekly slots,
 * with start_min/end_min/name/broadcaster/slug). This widget reads it on
 * every page render, computes Israel-time "now/next" from the slot list,
 * and emits the matching .hp-live-now markup. No more drift between the
 * homepage and the actual schedule.
 *
 * Falls back gracefully: if the option is missing or empty, returns ''
 * instead of stale HTML — so you can also use this on staging without
 * Rocky pushing.
 */

if ( ! function_exists( 'zerock_render_now_playing' ) ) {

	function zerock_render_now_playing() {
		$raw  = get_option( 'zerock_now_playing_json', '' );
		$data = is_string( $raw ) ? json_decode( $raw, true ) : null;
		if ( ! is_array( $data ) || empty( $data['slots'] ) ) {
			return ''; // No live data — render nothing rather than stale HTML.
		}

		// Always compute in Israel time, regardless of WP server timezone.
		try {
			$tz = new DateTimeZone( $data['tz'] ?? 'Asia/Jerusalem' );
		} catch ( Exception $e ) {
			$tz = new DateTimeZone( 'Asia/Jerusalem' );
		}
		$now   = new DateTime( 'now', $tz );
		$today = (int) $now->format( 'w' );                      // 0=Sun..6=Sat
		$now_m = ( (int) $now->format( 'H' ) ) * 60 + (int) $now->format( 'i' );

		// Bucket slots by day, sorted by start time.
		$by_day = array_fill( 0, 7, array() );
		foreach ( $data['slots'] as $s ) {
			$d = isset( $s['day'] ) ? (int) $s['day'] : -1;
			if ( $d >= 0 && $d <= 6 ) {
				$by_day[ $d ][] = $s;
			}
		}
		foreach ( $by_day as $d => $arr ) {
			usort( $by_day[ $d ], function ( $a, $b ) {
				return ( (int) $a['start_min'] ) - ( (int) $b['start_min'] );
			} );
		}

		// Current slot: today's slot whose [start, end) covers now.
		$current = null;
		foreach ( $by_day[ $today ] as $s ) {
			if ( $s['start_min'] <= $now_m && $now_m < $s['end_min'] ) {
				$current = $s;
				break;
			}
		}

		// Next slot: today's first start > now, else first slot of any later day.
		$next = null;
		foreach ( $by_day[ $today ] as $s ) {
			if ( $s['start_min'] > $now_m ) {
				$next = $s;
				break;
			}
		}
		if ( ! $next ) {
			for ( $i = 1; $i <= 7 && ! $next; $i++ ) {
				$d = ( $today + $i ) % 7;
				if ( ! empty( $by_day[ $d ] ) ) {
					$next = $by_day[ $d ][0];
				}
			}
		}

		$fmt_time = function ( $a, $b ) {
			return sprintf(
				'%02d:%02d - %02d:%02d',
				intval( $a / 60 ), $a % 60,
				intval( $b / 60 ), $b % 60
			);
		};

		// Best-effort featured-image lookup by slug. Tries 'show', 'shows',
		// 'page', and 'post' types so it works regardless of how the show
		// archive is implemented. Returns '' if nothing found — caller then
		// renders without the .hp-left img block.
		$img_for = function ( $slot ) {
			if ( empty( $slot['slug'] ) ) {
				return '';
			}
			$post = get_page_by_path( $slot['slug'], OBJECT, array( 'show', 'shows', 'page', 'post' ) );
			if ( $post ) {
				$url = get_the_post_thumbnail_url( $post->ID, 'medium' );
				if ( $url ) {
					return $url;
				}
			}
			return '';
		};

		ob_start();
		?>
		<div class="hp-live-now-title">עכשיו בשידור</div>
		<div class="hp-live-now">
			<div class="hp-right">
				<?php if ( $current ) : ?>
					<span class="hp-show-name"><?php echo esc_html( $current['name'] ); ?></span>
					<?php if ( ! empty( $current['broadcaster'] ) ) : ?>
						<span class="hp-show-text"><?php echo esc_html( $current['broadcaster'] ); ?></span>
					<?php endif; ?>
					<span class="hp-show-time">
						<?php echo esc_html( $fmt_time( $current['start_min'], $current['end_min'] ) ); ?>
					</span>
				<?php else : ?>
					<span class="hp-show-name">רוק ברצף</span>
					<span class="hp-show-text">רוקי</span>
				<?php endif; ?>
			</div>
			<?php if ( $current ) :
				$img = $img_for( $current );
				if ( $img ) : ?>
				<div class="hp-left">
					<img src="<?php echo esc_url( $img ); ?>" />
				</div>
			<?php endif; endif; ?>
		</div>

		<?php if ( $next ) : ?>
			<div class="hp-next-show-title">התכנית הבאה</div>
			<div class="hp-next-show">
				<div class="hp-right">
					<span class="hp-show-name"><?php echo esc_html( $next['name'] ); ?></span>
					<?php if ( ! empty( $next['broadcaster'] ) ) : ?>
						<span class="hp-show-text"><?php echo esc_html( $next['broadcaster'] ); ?></span>
					<?php endif; ?>
					<span class="hp-show-time">
						<?php echo esc_html( $fmt_time( $next['start_min'], $next['end_min'] ) ); ?>
					</span>
				</div>
				<?php $img = $img_for( $next ); if ( $img ) : ?>
					<div class="hp-left">
						<img src="<?php echo esc_url( $img ); ?>" />
					</div>
				<?php endif; ?>
			</div>
		<?php endif; ?>

		<div class="hp-button-schedule">
			<a class="pagelink" href="/schedule/">לוח השידורים המלא</a>
		</div>
		<?php
		return ob_get_clean();
	}

}

// Register the shortcode. (Outside the function_exists guard so re-saving the
// snippet always re-registers; WP handles re-registration cleanly.)
add_shortcode( 'zerock_now_playing', 'zerock_render_now_playing' );
