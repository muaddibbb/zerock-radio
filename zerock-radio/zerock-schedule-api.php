<?php
/**
 * Plugin Name: ZeRock Schedule API
 * Description: REST endpoint for Rocky server to push schedule HTML to the WP board.
 *              Install: upload to wp-content/mu-plugins/ (no activation needed).
 *              Then add ONE line to page-schedule.php where the grid should appear:
 *                  echo get_option('zerock_board_html', '');
 * Version: 1.0
 */

if (!defined('ABSPATH')) exit;

add_action('rest_api_init', function () {
    register_rest_route('zerock/v1', '/schedule', [
        'methods'             => 'POST',
        'callback'            => 'zerock_update_schedule_html',
        'permission_callback' => function () {
            return current_user_can('manage_options');
        },
    ]);
});

function zerock_update_schedule_html(WP_REST_Request $req) {
    $body = json_decode($req->get_body(), true);
    $html = isset($body['html']) ? $body['html'] : '';

    if (empty($html)) {
        return new WP_Error('empty_html', 'html field is required', ['status' => 400]);
    }

    // 1. Store as a dedicated WP option (template reads this via get_option)
    update_option('zerock_board_html', $html, false);

    // 2. Also update the schedule page content so it renders via the_content()
    //    Page ID 254 is the /schedule/ page. This is a no-op if the theme
    //    doesn't call the_content(), but harmless either way.
    $post_id = 254;
    $result  = wp_update_post([
        'ID'           => $post_id,
        'post_content' => $html,
        'post_status'  => 'publish',
    ], true);

    if (is_wp_error($result)) {
        return new WP_Error('update_failed', $result->get_error_message(), ['status' => 500]);
    }

    return ['ok' => true, 'updated_post' => $post_id, 'option' => 'zerock_board_html'];
}
