-- catchap (백엔드) 표 구조 — ★데이터 없음
-- 2026-08-12 · 살아 있는 DB 에서 SHOW CREATE TABLE 로 그대로 뽑음
-- 표 구조는 alembic 이 만들지만, 실제로 돌던 상태를 그대로 남겨 둔다.
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS=0;

DROP TABLE IF EXISTS `ai_attempt_features`;
CREATE TABLE `ai_attempt_features` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `attempt_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `feature_version` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1',
  `reaction_time_ms` double DEFAULT NULL,
  `total_duration_ms` double DEFAULT NULL,
  `active_duration_ms` double DEFAULT NULL,
  `idle_duration_ms` double DEFAULT NULL,
  `pointer_event_count` int unsigned DEFAULT NULL,
  `pointer_move_count` int unsigned DEFAULT NULL,
  `pointer_down_count` int unsigned DEFAULT NULL,
  `pointer_up_count` int unsigned DEFAULT NULL,
  `drag_count` int unsigned DEFAULT NULL,
  `drop_count` int unsigned DEFAULT NULL,
  `wrong_click_count` int unsigned DEFAULT NULL,
  `wrong_drag_count` int unsigned DEFAULT NULL,
  `correction_count` int unsigned DEFAULT NULL,
  `object_revisit_count` int unsigned DEFAULT NULL,
  `hesitation_count` int unsigned DEFAULT NULL,
  `pause_count` int unsigned DEFAULT NULL,
  `total_path_length` double DEFAULT NULL,
  `drag_path_length` double DEFAULT NULL,
  `straight_line_distance` double DEFAULT NULL,
  `straightness_ratio` double DEFAULT NULL,
  `average_speed` double DEFAULT NULL,
  `max_speed` double DEFAULT NULL,
  `speed_stddev` double DEFAULT NULL,
  `average_acceleration` double DEFAULT NULL,
  `max_acceleration` double DEFAULT NULL,
  `average_jerk` double DEFAULT NULL,
  `average_curvature` double DEFAULT NULL,
  `direction_change_count` int unsigned DEFAULT NULL,
  `movement_entropy` double DEFAULT NULL,
  `extraction_status` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'completed',
  `extraction_error` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `extra_features` json DEFAULT NULL,
  `extracted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ai_attempt_features_attempt_version` (`attempt_id`,`feature_version`),
  KEY `idx_ai_attempt_features_attempt_id` (`attempt_id`),
  KEY `idx_ai_attempt_features_feature_version` (`feature_version`),
  KEY `idx_ai_attempt_features_extraction_status` (`extraction_status`),
  CONSTRAINT `fk_ai_attempt_features_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `ai_behavior_attempts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `chk_ai_attempt_features_extraction_status` CHECK ((`extraction_status` in (_utf8mb4'pending',_utf8mb4'completed',_utf8mb4'failed'))),
  CONSTRAINT `chk_ai_attempt_features_straightness` CHECK (((`straightness_ratio` is null) or (`straightness_ratio` between 0 and 1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_behavior_attempts`;
CREATE TABLE `ai_behavior_attempts` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `challenge_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `session_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `participant_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `attempt_number` int unsigned NOT NULL DEFAULT '1',
  `label` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `label_source` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `bot_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `quality_status` varchar(24) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `quality_reason` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `verification_result` varchar(24) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `is_correct` tinyint(1) DEFAULT NULL,
  `expected_target_count` int unsigned DEFAULT NULL,
  `selected_object_count` int unsigned DEFAULT NULL,
  `selected_object_ids` json DEFAULT NULL,
  `device_type` varchar(24) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `pointer_type` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `browser_name` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `operating_system` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `viewport_width` int unsigned DEFAULT NULL,
  `viewport_height` int unsigned DEFAULT NULL,
  `screen_width` int unsigned DEFAULT NULL,
  `screen_height` int unsigned DEFAULT NULL,
  `device_pixel_ratio` double DEFAULT NULL,
  `started_at` datetime(6) NOT NULL,
  `submitted_at` datetime(6) DEFAULT NULL,
  `completed_at` datetime(6) DEFAULT NULL,
  `reaction_time_ms` int unsigned DEFAULT NULL,
  `total_duration_ms` int unsigned DEFAULT NULL,
  `consent_version` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `metadata` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ai_behavior_attempts_challenge_attempt` (`challenge_id`,`attempt_number`),
  KEY `idx_ai_behavior_attempts_challenge_id` (`challenge_id`),
  KEY `idx_ai_behavior_attempts_session_id` (`session_id`),
  KEY `idx_ai_behavior_attempts_participant_id` (`participant_id`),
  KEY `idx_ai_behavior_attempts_label` (`label`),
  KEY `idx_ai_behavior_attempts_quality_status` (`quality_status`),
  KEY `idx_ai_behavior_attempts_label_source` (`label_source`),
  KEY `idx_ai_behavior_attempts_created_at` (`created_at`),
  CONSTRAINT `chk_ai_behavior_attempts_label` CHECK (((`label` is null) or (`label` in (_utf8mb4'human',_utf8mb4'bot')))),
  CONSTRAINT `chk_ai_behavior_attempts_pointer_type` CHECK (((`pointer_type` is null) or (`pointer_type` in (_utf8mb4'mouse',_utf8mb4'touch',_utf8mb4'pen',_utf8mb4'unknown')))),
  CONSTRAINT `chk_ai_behavior_attempts_quality_status` CHECK ((`quality_status` in (_utf8mb4'pending',_utf8mb4'valid',_utf8mb4'invalid',_utf8mb4'review',_utf8mb4'corrupted'))),
  CONSTRAINT `chk_ai_behavior_attempts_verification_result` CHECK ((`verification_result` in (_utf8mb4'pending',_utf8mb4'passed',_utf8mb4'failed',_utf8mb4'expired',_utf8mb4'error')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_captcha_challenges`;
CREATE TABLE `ai_captcha_challenges` (
  `id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `nonce_hash` char(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `session_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `question_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `challenge_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'object_drag',
  `purpose` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'signup',
  `status` varchar(24) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'issued',
  `issued_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `expires_at` datetime(6) NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `completed_at` datetime(6) DEFAULT NULL,
  `consumed_at` datetime(6) DEFAULT NULL,
  `attempt_count` int unsigned NOT NULL DEFAULT '0',
  `max_attempts` int unsigned NOT NULL DEFAULT '3',
  `client_fingerprint_hash` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `client_ip_hash` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `metadata` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ai_captcha_challenges_nonce_hash` (`nonce_hash`),
  KEY `idx_ai_captcha_challenges_session_id` (`session_id`),
  KEY `idx_ai_captcha_challenges_question_id` (`question_id`),
  KEY `idx_ai_captcha_challenges_status` (`status`),
  KEY `idx_ai_captcha_challenges_expires_at` (`expires_at`),
  KEY `idx_ai_captcha_challenges_session_status` (`session_id`,`status`),
  CONSTRAINT `chk_ai_captcha_challenges_attempt_count` CHECK ((`attempt_count` <= `max_attempts`)),
  CONSTRAINT `chk_ai_captcha_challenges_expiration` CHECK ((`expires_at` > `issued_at`)),
  CONSTRAINT `chk_ai_captcha_challenges_status` CHECK ((`status` in (_utf8mb4'issued',_utf8mb4'in_progress',_utf8mb4'passed',_utf8mb4'failed',_utf8mb4'expired',_utf8mb4'consumed',_utf8mb4'cancelled')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_interaction_summaries`;
CREATE TABLE `ai_interaction_summaries` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `attempt_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `total_event_count` int unsigned NOT NULL DEFAULT '0',
  `pointer_move_count` int unsigned NOT NULL DEFAULT '0',
  `pointer_down_count` int unsigned NOT NULL DEFAULT '0',
  `pointer_up_count` int unsigned NOT NULL DEFAULT '0',
  `drag_start_count` int unsigned NOT NULL DEFAULT '0',
  `drag_move_count` int unsigned NOT NULL DEFAULT '0',
  `drop_count` int unsigned NOT NULL DEFAULT '0',
  `object_select_count` int unsigned NOT NULL DEFAULT '0',
  `object_remove_count` int unsigned NOT NULL DEFAULT '0',
  `wrong_click_count` int unsigned NOT NULL DEFAULT '0',
  `wrong_drag_count` int unsigned NOT NULL DEFAULT '0',
  `correction_count` int unsigned NOT NULL DEFAULT '0',
  `object_revisit_count` int unsigned NOT NULL DEFAULT '0',
  `regrab_count` int unsigned NOT NULL DEFAULT '0',
  `retry_count` int unsigned NOT NULL DEFAULT '0',
  `pointercancel_count` int unsigned NOT NULL DEFAULT '0',
  `empty_click_count` int unsigned NOT NULL DEFAULT '0',
  `failed_drop_count` int unsigned NOT NULL DEFAULT '0',
  `hesitation_count` int unsigned NOT NULL DEFAULT '0',
  `pause_count` int unsigned NOT NULL DEFAULT '0',
  `focus_count` int unsigned NOT NULL DEFAULT '0',
  `blur_count` int unsigned NOT NULL DEFAULT '0',
  `visibility_change_count` int unsigned NOT NULL DEFAULT '0',
  `reaction_time_ms` int unsigned DEFAULT NULL,
  `decision_time_ms` int unsigned DEFAULT NULL,
  `active_duration_ms` int unsigned DEFAULT NULL,
  `idle_duration_ms` int unsigned DEFAULT NULL,
  `total_duration_ms` int unsigned DEFAULT NULL,
  `calculated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `metadata` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ai_interaction_summaries_attempt_id` (`attempt_id`),
  KEY `idx_ai_interaction_summaries_calculated_at` (`calculated_at`),
  CONSTRAINT `fk_ai_interaction_summaries_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `ai_behavior_attempts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_model_configs`;
CREATE TABLE `ai_model_configs` (
  `id` char(36) NOT NULL,
  `provider` varchar(60) NOT NULL,
  `model_id` varchar(120) NOT NULL,
  `name` varchar(100) NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `cost_in_usd` float NOT NULL DEFAULT '0',
  `cost_out_usd` float NOT NULL DEFAULT '0',
  `tokens_in` bigint NOT NULL DEFAULT '0',
  `tokens_out` bigint NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `ai_model_predictions`;
CREATE TABLE `ai_model_predictions` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `attempt_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_version` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `feature_version` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `predicted_label` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `human_probability` double DEFAULT NULL,
  `bot_probability` double DEFAULT NULL,
  `model_score` double NOT NULL,
  `decision_threshold` double DEFAULT NULL,
  `risk_score` double NOT NULL,
  `risk_level` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `recommended_action` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `risk_reasons` json NOT NULL,
  `inference_latency_ms` int unsigned DEFAULT NULL,
  `model_metadata` json DEFAULT NULL,
  `predicted_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_ai_model_predictions_attempt_id` (`attempt_id`),
  KEY `idx_ai_model_predictions_model` (`model_name`,`model_version`),
  KEY `idx_ai_model_predictions_predicted_label` (`predicted_label`),
  KEY `idx_ai_model_predictions_risk_level` (`risk_level`),
  KEY `idx_ai_model_predictions_recommended_action` (`recommended_action`),
  KEY `idx_ai_model_predictions_predicted_at` (`predicted_at`),
  CONSTRAINT `fk_ai_model_predictions_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `ai_behavior_attempts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `chk_ai_model_predictions_bot_probability` CHECK (((`bot_probability` is null) or (`bot_probability` between 0 and 1))),
  CONSTRAINT `chk_ai_model_predictions_human_probability` CHECK (((`human_probability` is null) or (`human_probability` between 0 and 1))),
  CONSTRAINT `chk_ai_model_predictions_label` CHECK ((`predicted_label` in (_utf8mb4'human',_utf8mb4'bot',_utf8mb4'uncertain'))),
  CONSTRAINT `chk_ai_model_predictions_recommended_action` CHECK ((`recommended_action` in (_utf8mb4'allow',_utf8mb4'step_up',_utf8mb4'step_up_and_rate_limit'))),
  CONSTRAINT `chk_ai_model_predictions_risk_score` CHECK ((`risk_score` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_pointer_events`;
CREATE TABLE `ai_pointer_events` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `attempt_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `seq` int unsigned NOT NULL,
  `event_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `object_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `client_timestamp_ms` bigint unsigned NOT NULL,
  `elapsed_ms` int unsigned DEFAULT NULL,
  `x_normalized` double DEFAULT NULL,
  `y_normalized` double DEFAULT NULL,
  `x_pixel` double DEFAULT NULL,
  `y_pixel` double DEFAULT NULL,
  `movement_x` double DEFAULT NULL,
  `movement_y` double DEFAULT NULL,
  `pressure` double DEFAULT NULL,
  `button_code` smallint DEFAULT NULL,
  `buttons_mask` smallint DEFAULT NULL,
  `pointer_type` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `viewport_width` int unsigned DEFAULT NULL,
  `viewport_height` int unsigned DEFAULT NULL,
  `event_metadata` json DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ai_pointer_events_attempt_seq` (`attempt_id`,`seq`),
  KEY `idx_ai_pointer_events_attempt_id` (`attempt_id`),
  KEY `idx_ai_pointer_events_event_type` (`event_type`),
  KEY `idx_ai_pointer_events_attempt_timestamp` (`attempt_id`,`client_timestamp_ms`),
  CONSTRAINT `fk_ai_pointer_events_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `ai_behavior_attempts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `chk_ai_pointer_events_normalized_x` CHECK (((`x_normalized` is null) or (`x_normalized` between 0 and 1))),
  CONSTRAINT `chk_ai_pointer_events_normalized_y` CHECK (((`y_normalized` is null) or (`y_normalized` between 0 and 1))),
  CONSTRAINT `chk_ai_pointer_events_pressure` CHECK (((`pressure` is null) or (`pressure` between 0 and 1)))
) ENGINE=InnoDB AUTO_INCREMENT=1023 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_predictions`;
CREATE TABLE `ai_predictions` (
  `asset_id` char(36) NOT NULL,
  `model_version` varchar(30) NOT NULL,
  `predicted_label` varchar(60) NOT NULL,
  `confidence` float NOT NULL,
  `latency_ms` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_ai_predictions_asset_id` (`asset_id`),
  CONSTRAINT `ai_predictions_ibfk_1` FOREIGN KEY (`asset_id`) REFERENCES `captcha_assets` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `ai_security_features`;
CREATE TABLE `ai_security_features` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `attempt_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `replay_similarity_score` double DEFAULT NULL,
  `exact_sequence_match` tinyint(1) NOT NULL DEFAULT '0',
  `nonce_reuse_detected` tinyint(1) NOT NULL DEFAULT '0',
  `challenge_reuse_detected` tinyint(1) NOT NULL DEFAULT '0',
  `session_mismatch_detected` tinyint(1) NOT NULL DEFAULT '0',
  `timestamp_anomaly_detected` tinyint(1) NOT NULL DEFAULT '0',
  `event_order_anomaly_detected` tinyint(1) NOT NULL DEFAULT '0',
  `webdriver_detected` tinyint(1) NOT NULL DEFAULT '0',
  `headless_browser_detected` tinyint(1) NOT NULL DEFAULT '0',
  `automation_property_detected` tinyint(1) NOT NULL DEFAULT '0',
  `duplicate_fingerprint_count` int unsigned NOT NULL DEFAULT '0',
  `recent_session_attempt_count` int unsigned NOT NULL DEFAULT '0',
  `recent_challenge_attempt_count` int unsigned NOT NULL DEFAULT '0',
  `recent_failure_count` int unsigned NOT NULL DEFAULT '0',
  `session_frequency_score` double DEFAULT NULL,
  `challenge_frequency_score` double DEFAULT NULL,
  `device_fingerprint_hash` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ip_hash` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_agent_hash` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `security_flags` json DEFAULT NULL,
  `calculated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_ai_security_features_attempt_id` (`attempt_id`),
  KEY `idx_ai_security_features_replay_similarity` (`replay_similarity_score`),
  KEY `idx_ai_security_features_device_fingerprint` (`device_fingerprint_hash`),
  KEY `idx_ai_security_features_ip_hash` (`ip_hash`),
  CONSTRAINT `fk_ai_security_features_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `ai_behavior_attempts` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `chk_ai_security_features_replay_score` CHECK (((`replay_similarity_score` is null) or (`replay_similarity_score` between 0 and 1)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `ai_shadow_outcomes`;
CREATE TABLE `ai_shadow_outcomes` (
  `attempt_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `main_captcha_verdict` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `final_verdict` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `would_have_action` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `risk_level` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `model_version` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `recorded_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`attempt_id`),
  KEY `idx_ai_shadow_outcomes_action` (`would_have_action`),
  KEY `idx_ai_shadow_outcomes_recorded_at` (`recorded_at`),
  CONSTRAINT `fk_ai_shadow_outcomes_attempt` FOREIGN KEY (`attempt_id`) REFERENCES `ai_behavior_attempts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `chk_ai_shadow_outcomes_action` CHECK ((`would_have_action` in (_utf8mb4'allow',_utf8mb4'step_up',_utf8mb4'step_up_and_rate_limit'))),
  CONSTRAINT `chk_ai_shadow_outcomes_final_verdict` CHECK ((`final_verdict` in (_utf8mb4'passed',_utf8mb4'failed'))),
  CONSTRAINT `chk_ai_shadow_outcomes_main_verdict` CHECK ((`main_captcha_verdict` in (_utf8mb4'passed',_utf8mb4'failed')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `alembic_version`;
CREATE TABLE `alembic_version` (
  `version_num` varchar(32) NOT NULL,
  PRIMARY KEY (`version_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `api_keys`;
CREATE TABLE `api_keys` (
  `organization_id` char(36) NOT NULL,
  `site_id` char(36) NOT NULL,
  `site_key` varchar(64) NOT NULL,
  `secret_key_hash` varchar(64) NOT NULL,
  `status` varchar(20) NOT NULL,
  `last_used_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `product` varchar(20) NOT NULL DEFAULT 'captcha',
  `subject` varchar(20) DEFAULT NULL,
  `label` varchar(100) DEFAULT NULL,
  `first_party` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_api_keys_site_key` (`site_key`),
  KEY `ix_api_keys_organization_id` (`organization_id`),
  KEY `ix_api_keys_site_id` (`site_id`),
  CONSTRAINT `api_keys_ibfk_1` FOREIGN KEY (`site_id`) REFERENCES `sites` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `api_usage_logs`;
CREATE TABLE `api_usage_logs` (
  `organization_id` char(36) NOT NULL,
  `site_id` char(36) DEFAULT NULL,
  `endpoint` varchar(150) NOT NULL,
  `method` varchar(10) NOT NULL,
  `status_code` int NOT NULL,
  `latency_ms` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `api_key_id` char(36) DEFAULT NULL,
  `product` varchar(20) DEFAULT NULL,
  `subject` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_api_usage_logs_organization_id` (`organization_id`),
  KEY `ix_api_usage_logs_site_id` (`site_id`),
  KEY `ix_aul_org_created` (`organization_id`,`created_at`),
  KEY `ix_aul_api_key_id` (`api_key_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `audit_logs`;
CREATE TABLE `audit_logs` (
  `organization_id` char(36) DEFAULT NULL,
  `actor_user_id` char(36) DEFAULT NULL,
  `action` varchar(60) NOT NULL,
  `target_type` varchar(40) DEFAULT NULL,
  `target_id` char(36) DEFAULT NULL,
  `before_json` json DEFAULT NULL,
  `after_json` json DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_audit_logs_action` (`action`),
  KEY `ix_audit_logs_actor_user_id` (`actor_user_id`),
  KEY `ix_audit_logs_organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `badges`;
CREATE TABLE `badges` (
  `name` varchar(60) NOT NULL,
  `description` varchar(200) NOT NULL,
  `icon` varchar(60) NOT NULL,
  `color` varchar(20) NOT NULL,
  `condition_text` varchar(200) NOT NULL,
  `order_no` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_badge_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `behavior_summaries`;
CREATE TABLE `behavior_summaries` (
  `organization_id` char(36) NOT NULL,
  `student_id` char(36) DEFAULT NULL,
  `source_type` varchar(30) NOT NULL,
  `solve_time_ms` int NOT NULL,
  `path_length` float NOT NULL,
  `avg_speed` float NOT NULL,
  `pause_count` int NOT NULL,
  `retry_count` int NOT NULL,
  `drop_distance_norm` float NOT NULL,
  `interaction_result` varchar(20) DEFAULT NULL,
  `risk_level` varchar(20) NOT NULL,
  `occurred_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `dataset_status` varchar(20) NOT NULL DEFAULT 'candidate',
  `input_type` varchar(10) NOT NULL DEFAULT 'unknown',
  `sample_label` varchar(12) NOT NULL DEFAULT 'organic',
  `actor_band` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_behavior_summaries_organization_id` (`organization_id`),
  KEY `ix_behavior_summaries_student_id` (`student_id`),
  KEY `ix_bs_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `behavior_traces`;
CREATE TABLE `behavior_traces` (
  `id` char(36) NOT NULL,
  `behavior_id` char(36) NOT NULL,
  `points` json NOT NULL,
  `point_count` int NOT NULL DEFAULT '0',
  `duration_ms` int NOT NULL DEFAULT '0',
  `box_w` int NOT NULL DEFAULT '0',
  `box_h` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_behavior_traces_behavior_id` (`behavior_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `captcha_assets`;
CREATE TABLE `captcha_assets` (
  `organization_id` char(36) DEFAULT NULL,
  `file_url` varchar(255) NOT NULL,
  `file_name` varchar(255) NOT NULL,
  `file_type` varchar(30) NOT NULL,
  `category` varchar(30) DEFAULT NULL,
  `ai_label` varchar(60) DEFAULT NULL,
  `review_status` varchar(20) NOT NULL,
  `approved_by` char(36) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_captcha_assets_organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `captcha_attempts`;
CREATE TABLE `captcha_attempts` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `challenge_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `selected_object_ids` json NOT NULL,
  `is_correct` tinyint(1) NOT NULL,
  `failure_reason` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `duration_ms` int unsigned NOT NULL,
  `behavior_summary` json DEFAULT NULL,
  `raw_event_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_attempt_challenge` (`challenge_id`),
  CONSTRAINT `fk_attempt_challenge` FOREIGN KEY (`challenge_id`) REFERENCES `captcha_challenges_v2` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=42 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `captcha_challenge_objects`;
CREATE TABLE `captcha_challenge_objects` (
  `challenge_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `object_id` bigint unsigned NOT NULL,
  `temporary_object_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`challenge_id`,`temporary_object_id`),
  UNIQUE KEY `uq_challenge_object` (`challenge_id`,`object_id`),
  KEY `fk_map_object` (`object_id`),
  CONSTRAINT `fk_map_challenge` FOREIGN KEY (`challenge_id`) REFERENCES `captcha_challenges_v2` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_map_object` FOREIGN KEY (`object_id`) REFERENCES `captcha_objects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `captcha_challenges_v2`;
CREATE TABLE `captcha_challenges_v2` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `question_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `session_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `purpose` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `expires_at` datetime(6) NOT NULL,
  `attempt_count` tinyint unsigned NOT NULL DEFAULT '0',
  `status` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'issued',
  `created_at` datetime(6) NOT NULL,
  `verified_at` datetime(6) DEFAULT NULL,
  `client_ip_hash` char(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_challenge_expiry` (`expires_at`),
  KEY `idx_challenge_rate` (`client_ip_hash`,`created_at`),
  KEY `fk_challenge_question` (`question_id`),
  KEY `idx_challenge_session` (`session_id`,`created_at`),
  CONSTRAINT `fk_challenge_question` FOREIGN KEY (`question_id`) REFERENCES `captcha_questions` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `captcha_consumed_tokens`;
CREATE TABLE `captcha_consumed_tokens` (
  `id` char(36) NOT NULL,
  `kind` varchar(20) NOT NULL,
  `token_id` varchar(64) NOT NULL,
  `expires_at` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_captcha_consumed` (`kind`,`token_id`),
  KEY `ix_captcha_consumed_kind` (`kind`),
  KEY `ix_captcha_consumed_token_id` (`token_id`),
  KEY `ix_captcha_consumed_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `captcha_objects`;
CREATE TABLE `captcha_objects` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `question_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `object_key` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `label` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `bbox_x` double NOT NULL,
  `bbox_y` double NOT NULL,
  `bbox_width` double NOT NULL,
  `bbox_height` double NOT NULL,
  `role` varchar(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `piece_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_question_object` (`question_id`,`object_key`),
  KEY `idx_object_question` (`question_id`),
  CONSTRAINT `fk_object_question` FOREIGN KEY (`question_id`) REFERENCES `captcha_questions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=31379 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `captcha_questions`;
CREATE TABLE `captcha_questions` (
  `id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `instruction_ko` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `instruction_en` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `source` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_question_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `image_path` varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `image_width` int unsigned NOT NULL,
  `image_height` int unsigned NOT NULL,
  `difficulty` tinyint unsigned NOT NULL DEFAULT '2',
  `status` varchar(24) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'draft',
  `review_status` varchar(24) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending',
  `reviewer` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `reviewed_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_question_status` (`status`,`review_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `captcha_settings`;
CREATE TABLE `captcha_settings` (
  `organization_id` char(36) NOT NULL,
  `active_types` json NOT NULL,
  `round_count` int NOT NULL,
  `shuffle` tinyint(1) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_captcha_settings_organization_id` (`organization_id`),
  CONSTRAINT `captcha_settings_ibfk_1` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `captcha_store`;
CREATE TABLE `captcha_store` (
  `id` char(36) NOT NULL,
  `k` varchar(64) NOT NULL,
  `kind` varchar(16) NOT NULL,
  `payload` json DEFAULT NULL,
  `used` tinyint(1) NOT NULL DEFAULT '0',
  `expires_at` datetime NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_captcha_k` (`k`),
  KEY `ix_captcha_kind` (`kind`),
  KEY `ix_captcha_exp` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `captcha_tokens`;
CREATE TABLE `captcha_tokens` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `challenge_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `token_hash` char(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `purpose` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `session_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `expires_at` datetime(6) NOT NULL,
  `consumed_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_hash` (`token_hash`),
  KEY `fk_token_challenge` (`challenge_id`),
  CONSTRAINT `fk_token_challenge` FOREIGN KEY (`challenge_id`) REFERENCES `captcha_challenges_v2` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=39 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `chapter_progress`;
CREATE TABLE `chapter_progress` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `chapter_no` int NOT NULL,
  `stages_done` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_chapter_progress` (`student_id`,`subject`,`chapter_no`),
  KEY `ix_chapter_progress_student_id` (`student_id`),
  KEY `ix_chapter_progress_subject` (`subject`),
  CONSTRAINT `chapter_progress_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `chapters`;
CREATE TABLE `chapters` (
  `subject` varchar(20) NOT NULL,
  `order_no` int NOT NULL,
  `name` varchar(100) NOT NULL,
  `total_questions` int NOT NULL,
  `concept` json NOT NULL,
  `status` varchar(20) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_chapters_subject` (`subject`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `class_assignments`;
CREATE TABLE `class_assignments` (
  `id` char(36) NOT NULL,
  `organization_id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `class_id` char(36) NOT NULL,
  `started_on` datetime NOT NULL,
  `ended_on` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_ca_student` (`student_id`),
  KEY `ix_ca_class` (`class_id`),
  KEY `ix_ca_org` (`organization_id`),
  CONSTRAINT `fk_ca_class` FOREIGN KEY (`class_id`) REFERENCES `classes` (`id`),
  CONSTRAINT `fk_ca_student` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `classes`;
CREATE TABLE `classes` (
  `organization_id` char(36) NOT NULL,
  `name` varchar(50) NOT NULL,
  `grade` int DEFAULT NULL,
  `age_group` varchar(30) DEFAULT NULL,
  `teacher_id` char(36) DEFAULT NULL,
  `status` varchar(20) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `assistant_teacher_id` char(36) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_classes_organization_id` (`organization_id`),
  KEY `ix_classes_teacher_id` (`teacher_id`),
  CONSTRAINT `classes_ibfk_1` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`),
  CONSTRAINT `classes_ibfk_2` FOREIGN KEY (`teacher_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `coin_transactions`;
CREATE TABLE `coin_transactions` (
  `student_id` char(36) NOT NULL,
  `amount` int NOT NULL,
  `reason` varchar(100) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_coin_transactions_student_id` (`student_id`),
  CONSTRAINT `coin_transactions_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `concept_reads`;
CREATE TABLE `concept_reads` (
  `student_id` char(36) NOT NULL,
  `chapter_id` char(36) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_concept_reads_chapter_id` (`chapter_id`),
  KEY `ix_concept_reads_student_id` (`student_id`),
  CONSTRAINT `concept_reads_ibfk_1` FOREIGN KEY (`chapter_id`) REFERENCES `chapters` (`id`),
  CONSTRAINT `concept_reads_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `consents`;
CREATE TABLE `consents` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `student_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `granted_by_user_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `consent_type` varchar(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'personal_info',
  `terms_version` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1',
  `granted_at` datetime NOT NULL,
  `withdrawn_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_consent_student` (`student_id`),
  KEY `ix_consent_org` (`organization_id`),
  KEY `ix_consent_grantor` (`granted_by_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `contents`;
CREATE TABLE `contents` (
  `organization_id` char(36) DEFAULT NULL,
  `title` varchar(150) NOT NULL,
  `description` text,
  `category` varchar(30) NOT NULL,
  `subject` varchar(20) DEFAULT NULL,
  `difficulty` int NOT NULL,
  `age_group` varchar(30) NOT NULL,
  `icon` varchar(60) DEFAULT NULL,
  `route_hint` varchar(120) DEFAULT NULL,
  `status` varchar(20) NOT NULL,
  `created_by` char(36) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_contents_category` (`category`),
  KEY `ix_contents_organization_id` (`organization_id`),
  KEY `ix_contents_subject` (`subject`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `course_completions`;
CREATE TABLE `course_completions` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `course_id` char(36) NOT NULL,
  `passed_at` datetime NOT NULL,
  `question_count` int NOT NULL DEFAULT '0',
  `sittings_count` int NOT NULL DEFAULT '0',
  `perfect` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_completion_student_course` (`student_id`,`course_id`),
  KEY `ix_cc_student_id` (`student_id`),
  KEY `ix_cc_course_id` (`course_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `course_enrollments`;
CREATE TABLE `course_enrollments` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `course_id` char(36) NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `enrolled_at` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_enroll_student_course` (`student_id`,`course_id`),
  KEY `ix_enroll_student` (`student_id`),
  KEY `ix_enroll_course` (`course_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `course_exam_attempts`;
CREATE TABLE `course_exam_attempts` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `course_id` char(36) NOT NULL,
  `question_id` char(36) NOT NULL,
  `sitting_id` char(36) NOT NULL,
  `result` varchar(10) NOT NULL,
  `answer` json DEFAULT NULL,
  `solve_time_ms` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_cea_student_id` (`student_id`),
  KEY `ix_cea_course_id` (`course_id`),
  KEY `ix_cea_question_id` (`question_id`),
  KEY `ix_cea_sitting_id` (`sitting_id`),
  KEY `ix_cea_student_course_q` (`student_id`,`course_id`,`question_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `course_exam_questions`;
CREATE TABLE `course_exam_questions` (
  `id` char(36) NOT NULL,
  `course_id` char(36) NOT NULL,
  `prompt` text NOT NULL,
  `options` json NOT NULL,
  `answer_indexes` json NOT NULL,
  `explain` text,
  `origin` varchar(20) NOT NULL DEFAULT 'manual',
  `source` varchar(300) DEFAULT NULL,
  `origin_lecture_question_id` char(36) DEFAULT NULL,
  `order_no` int NOT NULL DEFAULT '0',
  `status` varchar(10) NOT NULL DEFAULT 'draft',
  `created_by` char(36) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  `images` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_ceq_course_id` (`course_id`),
  KEY `ix_ceq_course_status` (`course_id`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `course_exam_sittings`;
CREATE TABLE `course_exam_sittings` (
  `id` char(36) NOT NULL,
  `course_id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `questions` json NOT NULL,
  `submitted_at` datetime DEFAULT NULL,
  `total` int DEFAULT NULL,
  `correct` int DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_ces_course_id` (`course_id`),
  KEY `ix_ces_student_id` (`student_id`),
  KEY `ix_ces_student_course` (`student_id`,`course_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `course_orders`;
CREATE TABLE `course_orders` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `course_id` char(36) NOT NULL,
  `order_uid` varchar(64) NOT NULL,
  `amount` int NOT NULL DEFAULT '0',
  `status` varchar(20) NOT NULL DEFAULT 'pending',
  `provider` varchar(20) NOT NULL DEFAULT 'mock',
  `payment_key` varchar(200) DEFAULT NULL,
  `method` varchar(30) DEFAULT NULL,
  `paid_at` datetime DEFAULT NULL,
  `fail_reason` varchar(200) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `callback_token_hash` varchar(255) DEFAULT NULL COMMENT 'PG 콜백 검증 토큰 해시',
  `provider_session` varchar(255) DEFAULT NULL COMMENT 'PG사 결제 세션 식별값',
  `receipt_url` varchar(2048) DEFAULT NULL COMMENT '결제 영수증 URL',
  `cancelled_at` datetime DEFAULT NULL COMMENT '결제 취소 일시',
  `cancel_reason` varchar(500) DEFAULT NULL COMMENT '결제 취소 사유',
  `provider_payment_key` varchar(255) DEFAULT NULL COMMENT 'PG사 결제 키',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_order_uid` (`order_uid`),
  KEY `ix_order_student` (`student_id`),
  KEY `ix_order_course` (`course_id`),
  KEY `ix_order_student_course_status` (`student_id`,`course_id`,`status`),
  KEY `ix_order_provider_payment_key` (`provider_payment_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `courses`;
CREATE TABLE `courses` (
  `id` char(36) NOT NULL,
  `instructor_id` char(36) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `title` varchar(200) NOT NULL,
  `description` text,
  `price` int NOT NULL DEFAULT '0' COMMENT '강의 정상 가격',
  `sale_price` int DEFAULT NULL COMMENT '강의 할인 가격',
  `sale_ends_at` datetime DEFAULT NULL COMMENT '할인 종료 일시',
  `order_no` int NOT NULL DEFAULT '0',
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `category` varchar(40) DEFAULT NULL,
  `thumbnail_ext` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_courses_instructor_id` (`instructor_id`),
  KEY `ix_course_subject_status` (`subject`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `daily_quiz_status`;
CREATE TABLE `daily_quiz_status` (
  `student_id` char(36) NOT NULL,
  `quiz_date` date NOT NULL,
  `subject` varchar(20) NOT NULL,
  `topic` varchar(100) DEFAULT NULL,
  `status` varchar(20) NOT NULL,
  `reward_coins` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_daily_quiz_student_date_subject` (`student_id`,`quiz_date`,`subject`),
  KEY `ix_daily_quiz_status_quiz_date` (`quiz_date`),
  KEY `ix_daily_quiz_status_student_id` (`student_id`),
  CONSTRAINT `daily_quiz_status_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `daily_rewards`;
CREATE TABLE `daily_rewards` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `kind` varchar(30) NOT NULL,
  `reward_date` date NOT NULL,
  `amount` int NOT NULL DEFAULT '0',
  `created_at` datetime DEFAULT (now()),
  `updated_at` datetime DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_daily_reward` (`student_id`,`kind`,`reward_date`),
  KEY `ix_daily_rewards_student_id` (`student_id`),
  KEY `ix_daily_rewards_kind` (`kind`),
  KEY `ix_daily_rewards_reward_date` (`reward_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `email_logs`;
CREATE TABLE `email_logs` (
  `user_id` char(36) DEFAULT NULL,
  `to_email` varchar(255) NOT NULL,
  `subject` varchar(200) NOT NULL,
  `status` varchar(20) NOT NULL,
  `error_message` text,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_email_logs_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `email_verification_codes`;
CREATE TABLE `email_verification_codes` (
  `email` varchar(255) NOT NULL,
  `user_id` char(36) DEFAULT NULL,
  `purpose` varchar(20) NOT NULL,
  `code_hash` varchar(64) NOT NULL,
  `expires_at` datetime NOT NULL,
  `used_at` datetime DEFAULT NULL,
  `verified_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_email_verification_codes_code_hash` (`code_hash`),
  KEY `ix_email_verification_codes_email` (`email`),
  KEY `ix_email_verification_codes_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `family_messages`;
CREATE TABLE `family_messages` (
  `organization_id` char(36) NOT NULL,
  `teacher_id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `message` text NOT NULL,
  `status` varchar(20) NOT NULL,
  `read_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_family_messages_organization_id` (`organization_id`),
  KEY `ix_family_messages_student_id` (`student_id`),
  KEY `ix_family_messages_teacher_id` (`teacher_id`),
  CONSTRAINT `family_messages_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`),
  CONSTRAINT `family_messages_ibfk_2` FOREIGN KEY (`teacher_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `inquiries`;
CREATE TABLE `inquiries` (
  `inquiry_type` varchar(30) NOT NULL,
  `name` varchar(100) NOT NULL,
  `affiliation` varchar(150) DEFAULT NULL,
  `email` varchar(255) NOT NULL,
  `content` text NOT NULL,
  `status` varchar(20) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `inquiry_replies`;
CREATE TABLE `inquiry_replies` (
  `id` char(36) NOT NULL,
  `inquiry_id` char(36) NOT NULL,
  `body` text NOT NULL,
  `answered_by` char(36) DEFAULT NULL,
  `email_status` varchar(20) NOT NULL DEFAULT 'sent',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_inquiry_replies_inquiry_id` (`inquiry_id`),
  CONSTRAINT `inquiry_replies_ibfk_1` FOREIGN KEY (`inquiry_id`) REFERENCES `inquiries` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `institutions`;
CREATE TABLE `institutions` (
  `name` varchar(150) NOT NULL,
  `inst_type` varchar(30) NOT NULL,
  `sido` varchar(30) NOT NULL,
  `sigungu` varchar(30) NOT NULL,
  `dong` varchar(30) NOT NULL,
  `road_address` varchar(255) NOT NULL,
  `organization_id` char(36) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_institutions_dong` (`dong`),
  KEY `ix_institutions_name` (`name`),
  KEY `ix_institutions_sido` (`sido`),
  KEY `ix_institutions_sigungu` (`sigungu`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `invitations`;
CREATE TABLE `invitations` (
  `organization_id` char(36) NOT NULL,
  `email` varchar(255) NOT NULL,
  `role` varchar(20) NOT NULL,
  `token_hash` varchar(64) NOT NULL,
  `invited_by` char(36) DEFAULT NULL,
  `expires_at` datetime NOT NULL,
  `accepted_at` datetime DEFAULT NULL,
  `status` varchar(20) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `teacher_code` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_hash` (`token_hash`),
  KEY `ix_invitations_email` (`email`),
  KEY `ix_invitations_organization_id` (`organization_id`),
  CONSTRAINT `invitations_ibfk_1` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `invoices`;
CREATE TABLE `invoices` (
  `organization_id` char(36) NOT NULL,
  `invoice_no` varchar(30) NOT NULL,
  `description` varchar(150) NOT NULL,
  `amount` int NOT NULL,
  `status` varchar(20) NOT NULL,
  `billed_on` varchar(20) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `invoice_no` (`invoice_no`),
  KEY `ix_invoices_organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `learning_attempts`;
CREATE TABLE `learning_attempts` (
  `organization_id` char(36) DEFAULT NULL,
  `student_id` char(36) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `chapter_no` int DEFAULT NULL,
  `content_id` varchar(80) DEFAULT NULL,
  `result` varchar(20) NOT NULL,
  `score` int NOT NULL,
  `solve_time_ms` int NOT NULL,
  `retry_count` int NOT NULL,
  `estimated_reason` varchar(50) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `graded` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`id`),
  KEY `ix_learning_attempts_organization_id` (`organization_id`),
  KEY `ix_learning_attempts_student_id` (`student_id`),
  KEY `ix_learning_attempts_subject` (`subject`),
  KEY `ix_la_student_created` (`student_id`,`created_at`),
  KEY `ix_la_org_created` (`organization_id`,`created_at`),
  KEY `ix_la_graded` (`graded`),
  CONSTRAINT `learning_attempts_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `learning_summaries`;
CREATE TABLE `learning_summaries` (
  `organization_id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `period_type` varchar(10) NOT NULL,
  `period_start` date NOT NULL,
  `period_end` date NOT NULL,
  `total_count` int NOT NULL,
  `correct_count` int NOT NULL,
  `average_solve_time_ms` int NOT NULL,
  `streak_days` int NOT NULL,
  `strength_tags` json NOT NULL,
  `need_practice_tags` json NOT NULL,
  `detail` json NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_learning_summaries_organization_id` (`organization_id`),
  KEY `ix_learning_summaries_student_id` (`student_id`),
  CONSTRAINT `learning_summaries_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_checkpoint_events`;
CREATE TABLE `lecture_checkpoint_events` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `position_sec` int NOT NULL DEFAULT '0',
  `result` varchar(20) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `question_id` char(36) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_lce_student_created` (`student_id`,`created_at`),
  KEY `ix_lce_lecture` (`lecture_id`),
  KEY `ix_lce_question` (`question_id`),
  CONSTRAINT `lecture_checkpoint_events_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`),
  CONSTRAINT `lecture_checkpoint_events_ibfk_2` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_materials`;
CREATE TABLE `lecture_materials` (
  `id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `title` varchar(200) NOT NULL,
  `kind` varchar(10) NOT NULL,
  `url` varchar(500) NOT NULL,
  `file_ext` varchar(10) DEFAULT NULL,
  `file_bytes` bigint NOT NULL DEFAULT '0',
  `order_no` int NOT NULL DEFAULT '0',
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_lm_lecture` (`lecture_id`),
  KEY `ix_lm_lecture_order` (`lecture_id`,`order_no`),
  CONSTRAINT `lecture_materials_ibfk_1` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_question_gen_jobs`;
CREATE TABLE `lecture_question_gen_jobs` (
  `id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `requested_by` char(36) NOT NULL,
  `n` int NOT NULL DEFAULT '3',
  `status` varchar(20) NOT NULL DEFAULT 'pending',
  `created_count` int NOT NULL DEFAULT '0',
  `transcript_used` tinyint(1) NOT NULL DEFAULT '0',
  `transcript_source` varchar(20) DEFAULT NULL,
  `self_verified` tinyint(1) NOT NULL DEFAULT '0',
  `captcha_candidates` int NOT NULL DEFAULT '0',
  `bank_candidates` int NOT NULL DEFAULT '0',
  `discard_candidates` int NOT NULL DEFAULT '0',
  `verify_error` varchar(255) DEFAULT NULL,
  `error_detail` text,
  `finished_at` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `phase` varchar(20) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_lqgj_lecture` (`lecture_id`),
  KEY `ix_lqgj_requested_by` (`requested_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_question_reports`;
CREATE TABLE `lecture_question_reports` (
  `id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `question_id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `reason` varchar(30) NOT NULL,
  `detail` varchar(500) DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'open',
  `resolved_by` char(36) DEFAULT NULL,
  `resolved_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_question_report` (`student_id`,`question_id`),
  KEY `ix_lqr_lecture_id` (`lecture_id`),
  KEY `ix_lqr_question_id` (`question_id`),
  KEY `ix_lqr_student_id` (`student_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_questions`;
CREATE TABLE `lecture_questions` (
  `id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `position_sec` int NOT NULL DEFAULT '0',
  `payload` json NOT NULL,
  `answer_index` int NOT NULL,
  `source` varchar(20) NOT NULL DEFAULT 'manual',
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `order_no` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `answer_indexes` json DEFAULT NULL,
  `content_start_sec` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_lq_lecture` (`lecture_id`),
  KEY `ix_lq_lecture_pos` (`lecture_id`,`position_sec`),
  CONSTRAINT `lecture_questions_ibfk_1` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_reviews`;
CREATE TABLE `lecture_reviews` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `rating` int NOT NULL,
  `text` text,
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_lecture_review` (`student_id`,`lecture_id`),
  KEY `ix_review_student` (`student_id`),
  KEY `ix_review_lecture_status` (`lecture_id`,`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_transcripts`;
CREATE TABLE `lecture_transcripts` (
  `id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `segments` json NOT NULL,
  `source` varchar(20) NOT NULL,
  `segment_count` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_lecture_transcript` (`lecture_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lecture_watch_progress`;
CREATE TABLE `lecture_watch_progress` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `watched_max_sec` int NOT NULL DEFAULT '0',
  `next_checkpoint_sec` int DEFAULT NULL,
  `checkpoints_passed` int NOT NULL DEFAULT '0',
  `status` varchar(20) NOT NULL DEFAULT 'watching',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `session_id` varchar(64) DEFAULT NULL,
  `last_heartbeat_at` datetime DEFAULT NULL,
  `exempt_streak` int NOT NULL DEFAULT '0',
  `checkpoint_fails` int NOT NULL DEFAULT '0',
  `bot_suspicion` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_lecture_watch` (`student_id`,`lecture_id`),
  KEY `ix_lwp_student` (`student_id`),
  KEY `ix_lwp_lecture` (`lecture_id`),
  CONSTRAINT `lecture_watch_progress_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`),
  CONSTRAINT `lecture_watch_progress_ibfk_2` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `lectures`;
CREATE TABLE `lectures` (
  `id` char(36) NOT NULL,
  `title` varchar(200) NOT NULL,
  `description` text,
  `subject` varchar(20) NOT NULL,
  `video_ext` varchar(10) NOT NULL,
  `video_bytes` bigint NOT NULL DEFAULT '0',
  `duration_sec` int NOT NULL DEFAULT '0',
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `uploaded_by` char(36) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `order_no` int NOT NULL DEFAULT '0',
  `course_id` char(36) DEFAULT NULL,
  `deleted_at` datetime DEFAULT NULL,
  `thumbnail_ext` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `uploaded_by` (`uploaded_by`),
  KEY `ix_lecture_subject_status` (`subject`,`status`),
  KEY `ix_lecture_created` (`created_at`),
  KEY `ix_lectures_course_id` (`course_id`),
  KEY `ix_lecture_status_deleted_at` (`status`,`deleted_at`),
  CONSTRAINT `lectures_ibfk_1` FOREIGN KEY (`uploaded_by`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `login_throttle`;
CREATE TABLE `login_throttle` (
  `identifier` varchar(255) NOT NULL,
  `fail_count` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_login_throttle_identifier` (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `memberships`;
CREATE TABLE `memberships` (
  `user_id` char(36) DEFAULT NULL,
  `organization_id` char(36) NOT NULL,
  `role` varchar(20) NOT NULL,
  `status` varchar(20) NOT NULL,
  `teacher_code` varchar(20) DEFAULT NULL,
  `position` varchar(50) DEFAULT NULL,
  `career_years` int DEFAULT NULL,
  `invited_by` char(36) DEFAULT NULL,
  `joined_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `managed_grade` int DEFAULT NULL,
  `pending_class` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `teacher_code` (`teacher_code`),
  UNIQUE KEY `uq_membership_user_org` (`user_id`,`organization_id`),
  KEY `ix_memberships_organization_id` (`organization_id`),
  KEY `ix_memberships_user_id` (`user_id`),
  CONSTRAINT `memberships_ibfk_1` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`),
  CONSTRAINT `memberships_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `model_versions`;
CREATE TABLE `model_versions` (
  `category` varchar(60) NOT NULL,
  `name` varchar(100) NOT NULL,
  `provider` varchar(60) NOT NULL,
  `version` varchar(30) NOT NULL,
  `status` varchar(20) NOT NULL,
  `description` text,
  `updated_on` varchar(30) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `notifications`;
CREATE TABLE `notifications` (
  `user_id` char(36) DEFAULT NULL,
  `student_id` char(36) DEFAULT NULL,
  `organization_id` char(36) DEFAULT NULL,
  `type` varchar(30) NOT NULL,
  `category` varchar(30) NOT NULL,
  `title` varchar(150) NOT NULL,
  `message` text NOT NULL,
  `child_id` char(36) DEFAULT NULL,
  `read_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_notifications_organization_id` (`organization_id`),
  KEY `ix_notifications_student_id` (`student_id`),
  KEY `ix_notifications_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `org_registration_requests`;
CREATE TABLE `org_registration_requests` (
  `org_name` varchar(150) NOT NULL,
  `org_type` varchar(30) NOT NULL,
  `business_number` varchar(30) DEFAULT NULL,
  `address` varchar(255) DEFAULT NULL,
  `contact_name` varchar(100) NOT NULL,
  `contact_email` varchar(255) NOT NULL,
  `contact_phone` varchar(30) DEFAULT NULL,
  `expected_students` varchar(30) DEFAULT NULL,
  `plan_interest` varchar(30) DEFAULT NULL,
  `status` varchar(20) NOT NULL,
  `approved_at` datetime DEFAULT NULL,
  `organization_id` char(36) DEFAULT NULL,
  `memo` text,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `organizations`;
CREATE TABLE `organizations` (
  `name` varchar(150) NOT NULL,
  `code` varchar(30) NOT NULL,
  `org_type` varchar(30) NOT NULL,
  `status` varchar(20) NOT NULL,
  `contact_email` varchar(255) DEFAULT NULL,
  `contact_phone` varchar(30) DEFAULT NULL,
  `address` varchar(255) DEFAULT NULL,
  `business_number` varchar(30) DEFAULT NULL,
  `code_expires_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `edu_subjects` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_organizations_code` (`code`),
  UNIQUE KEY `business_number` (`business_number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `parent_invite_codes`;
CREATE TABLE `parent_invite_codes` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `organization_id` char(36) NOT NULL,
  `code_hash` varchar(64) NOT NULL,
  `expires_at` datetime DEFAULT NULL,
  `max_uses` int NOT NULL DEFAULT '2',
  `used_count` int NOT NULL DEFAULT '0',
  `revoked_at` datetime DEFAULT NULL,
  `created_by` char(36) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_parent_invite_codes_student_id` (`student_id`),
  KEY `ix_parent_invite_codes_code_hash` (`code_hash`),
  KEY `ix_parent_invite_codes_organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `parent_student_links`;
CREATE TABLE `parent_student_links` (
  `parent_user_id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `organization_id` char(36) NOT NULL,
  `status` varchar(20) NOT NULL,
  `requested_at` datetime DEFAULT NULL,
  `approved_at` datetime DEFAULT NULL,
  `approved_by` char(36) DEFAULT NULL,
  `daily_goal` int NOT NULL,
  `time_limit_enabled` tinyint(1) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_parent_student_link` (`parent_user_id`,`student_id`),
  KEY `ix_parent_student_links_organization_id` (`organization_id`),
  KEY `ix_parent_student_links_parent_user_id` (`parent_user_id`),
  KEY `ix_parent_student_links_student_id` (`student_id`),
  CONSTRAINT `parent_student_links_ibfk_1` FOREIGN KEY (`parent_user_id`) REFERENCES `users` (`id`),
  CONSTRAINT `parent_student_links_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `password_reset_tokens`;
CREATE TABLE `password_reset_tokens` (
  `user_id` char(36) NOT NULL,
  `token_hash` varchar(64) NOT NULL,
  `expires_at` datetime NOT NULL,
  `used_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_hash` (`token_hash`),
  KEY `ix_password_reset_tokens_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `payment_methods`;
CREATE TABLE `payment_methods` (
  `organization_id` char(36) NOT NULL,
  `card_brand` varchar(30) NOT NULL,
  `card_last4` varchar(4) NOT NULL,
  `is_default` tinyint(1) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_payment_methods_organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `plans`;
CREATE TABLE `plans` (
  `key` varchar(30) NOT NULL,
  `name` varchar(60) NOT NULL,
  `monthly_price` int NOT NULL,
  `yearly_price` int NOT NULL,
  `api_quota` int NOT NULL,
  `student_seats` int NOT NULL,
  `teacher_seats` int NOT NULL,
  `features` json NOT NULL,
  `order_no` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `questions`;
CREATE TABLE `questions` (
  `id` varchar(80) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `type` varchar(30) NOT NULL,
  `order_no` int NOT NULL,
  `playable` tinyint(1) NOT NULL DEFAULT '1',
  `payload` json NOT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_q_subject` (`subject`),
  KEY `ix_q_type` (`type`),
  KEY `ix_q_order` (`order_no`),
  KEY `ix_q_playable` (`playable`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `recommendations`;
CREATE TABLE `recommendations` (
  `student_id` char(36) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `chapter_no` int NOT NULL,
  `priority` varchar(20) NOT NULL,
  `reason` text NOT NULL,
  `status` varchar(20) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_recommendations_student_id` (`student_id`),
  CONSTRAINT `recommendations_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `refresh_tokens`;
CREATE TABLE `refresh_tokens` (
  `user_id` char(36) NOT NULL,
  `subject_type` varchar(10) NOT NULL,
  `token_hash` varchar(64) NOT NULL,
  `expires_at` datetime NOT NULL,
  `revoked_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_hash` (`token_hash`),
  KEY `ix_refresh_tokens_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `report_download_logs`;
CREATE TABLE `report_download_logs` (
  `report_id` char(36) NOT NULL,
  `user_id` char(36) NOT NULL,
  `downloaded_at` datetime DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_report_download_logs_report_id` (`report_id`),
  KEY `ix_report_download_logs_user_id` (`user_id`),
  CONSTRAINT `report_download_logs_ibfk_1` FOREIGN KEY (`report_id`) REFERENCES `reports` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `reports`;
CREATE TABLE `reports` (
  `organization_id` char(36) NOT NULL,
  `student_id` char(36) DEFAULT NULL,
  `report_type` varchar(30) NOT NULL,
  `period_start` datetime DEFAULT NULL,
  `period_end` datetime DEFAULT NULL,
  `status` varchar(20) NOT NULL,
  `file_url` varchar(255) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_reports_organization_id` (`organization_id`),
  KEY `ix_reports_student_id` (`student_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `scratch_records`;
CREATE TABLE `scratch_records` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `organization_id` char(36) DEFAULT NULL,
  `subject` varchar(20) NOT NULL,
  `content_id` varchar(80) DEFAULT NULL,
  `strokes` json DEFAULT NULL,
  `stroke_count` int NOT NULL DEFAULT '0',
  `distance_px` int NOT NULL DEFAULT '0',
  `first_write_ms` int NOT NULL DEFAULT '0',
  `draw_ms` int NOT NULL DEFAULT '0',
  `purged` tinyint(1) NOT NULL DEFAULT '0',
  `consent_retain` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_scratch_student` (`student_id`),
  KEY `ix_scratch_org` (`organization_id`),
  KEY `ix_scratch_subject` (`subject`),
  KEY `ix_scratch_content` (`content_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `server_metric_hourly`;
CREATE TABLE `server_metric_hourly` (
  `id` char(36) NOT NULL,
  `server_key` varchar(40) NOT NULL,
  `hour` datetime NOT NULL,
  `samples` int NOT NULL DEFAULT '0',
  `cpu_sum` float NOT NULL DEFAULT '0',
  `mem_sum` float NOT NULL DEFAULT '0',
  `gpu_sum` float NOT NULL DEFAULT '0',
  `gpu_samples` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_smh_key` (`server_key`),
  KEY `ix_smh_key_hour` (`server_key`,`hour`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `server_metric_samples`;
CREATE TABLE `server_metric_samples` (
  `id` char(36) NOT NULL,
  `server_key` varchar(40) NOT NULL,
  `cpu_pct` float NOT NULL DEFAULT '0',
  `mem_pct` float NOT NULL DEFAULT '0',
  `gpu_util_pct` float DEFAULT NULL,
  `collected_at` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_sms_key` (`server_key`),
  KEY `ix_sms_key_time` (`server_key`,`collected_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `server_metrics`;
CREATE TABLE `server_metrics` (
  `id` char(36) NOT NULL,
  `server_key` varchar(40) NOT NULL,
  `label` varchar(60) NOT NULL,
  `host` varchar(80) DEFAULT NULL,
  `cpu_pct` float NOT NULL DEFAULT '0',
  `cpu_cores` int NOT NULL DEFAULT '0',
  `load1` float DEFAULT NULL,
  `mem_pct` float NOT NULL DEFAULT '0',
  `mem_used_mb` int NOT NULL DEFAULT '0',
  `mem_total_mb` int NOT NULL DEFAULT '0',
  `disk_pct` float NOT NULL DEFAULT '0',
  `disk_used_gb` float NOT NULL DEFAULT '0',
  `disk_total_gb` float NOT NULL DEFAULT '0',
  `gpu_present` tinyint(1) NOT NULL DEFAULT '0',
  `gpu_name` varchar(80) DEFAULT NULL,
  `gpu_util_pct` float DEFAULT NULL,
  `gpu_mem_used_mb` int DEFAULT NULL,
  `gpu_mem_total_mb` int DEFAULT NULL,
  `collected_at` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_server_metrics_key` (`server_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `shop_items`;
CREATE TABLE `shop_items` (
  `category` varchar(20) NOT NULL,
  `name` varchar(60) NOT NULL,
  `icon` varchar(60) NOT NULL,
  `price` int NOT NULL,
  `order_no` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_shop_items_category` (`category`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `sites`;
CREATE TABLE `sites` (
  `organization_id` char(36) NOT NULL,
  `name` varchar(150) NOT NULL,
  `domain` varchar(255) NOT NULL,
  `allowed_origins` json NOT NULL,
  `status` varchar(20) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_sites_organization_id` (`organization_id`),
  CONSTRAINT `sites_ibfk_1` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `social_accounts`;
CREATE TABLE `social_accounts` (
  `id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `student_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `user_id` char(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `provider` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `provider_user_id` varchar(191) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email_verified` tinyint(1) NOT NULL DEFAULT '0',
  `last_login_at` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_social_provider_user` (`provider`,`provider_user_id`),
  UNIQUE KEY `uq_social_student_provider` (`student_id`,`provider`),
  UNIQUE KEY `uq_social_user_provider` (`user_id`,`provider`),
  KEY `ix_social_student` (`student_id`),
  KEY `ix_social_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

DROP TABLE IF EXISTS `stat_blobs`;
CREATE TABLE `stat_blobs` (
  `organization_id` char(36) DEFAULT NULL,
  `key` varchar(80) NOT NULL,
  `payload` json NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_stat_org_key` (`organization_id`,`key`),
  KEY `ix_stat_blobs_key` (`key`),
  KEY `ix_stat_blobs_organization_id` (`organization_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `student_badges`;
CREATE TABLE `student_badges` (
  `student_id` char(36) NOT NULL,
  `badge_id` char(36) NOT NULL,
  `earned_at` datetime DEFAULT NULL,
  `progress` float NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_badge` (`student_id`,`badge_id`),
  KEY `ix_student_badges_badge_id` (`badge_id`),
  KEY `ix_student_badges_student_id` (`student_id`),
  CONSTRAINT `student_badges_ibfk_1` FOREIGN KEY (`badge_id`) REFERENCES `badges` (`id`),
  CONSTRAINT `student_badges_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `student_items`;
CREATE TABLE `student_items` (
  `student_id` char(36) NOT NULL,
  `item_id` char(36) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_item` (`student_id`,`item_id`),
  KEY `ix_student_items_item_id` (`item_id`),
  KEY `ix_student_items_student_id` (`student_id`),
  CONSTRAINT `student_items_ibfk_1` FOREIGN KEY (`item_id`) REFERENCES `shop_items` (`id`),
  CONSTRAINT `student_items_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `student_join_codes`;
CREATE TABLE `student_join_codes` (
  `id` char(36) NOT NULL,
  `organization_id` char(36) NOT NULL,
  `class_id` char(36) DEFAULT NULL,
  `login_id` varchar(60) NOT NULL,
  `code_hash` varchar(64) NOT NULL,
  `class_label` varchar(60) DEFAULT NULL,
  `expires_at` datetime DEFAULT NULL,
  `used_at` datetime DEFAULT NULL,
  `student_id` char(36) DEFAULT NULL,
  `created_by` char(36) DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `real_name` varchar(100) DEFAULT NULL,
  `gender` varchar(10) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sjc_login_id` (`login_id`),
  KEY `ix_student_join_codes_code_hash` (`code_hash`),
  KEY `ix_student_join_codes_organization_id` (`organization_id`),
  KEY `ix_student_join_codes_class_id` (`class_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `student_profiles`;
CREATE TABLE `student_profiles` (
  `organization_id` char(36) DEFAULT NULL,
  `class_id` char(36) DEFAULT NULL,
  `student_login_id` varchar(255) NOT NULL,
  `student_code` varchar(20) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `nickname` varchar(50) NOT NULL,
  `age` int DEFAULT NULL,
  `grade_band` varchar(30) NOT NULL,
  `avatar` json NOT NULL,
  `coins` int NOT NULL,
  `level` int NOT NULL,
  `status` varchar(20) NOT NULL,
  `last_login_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `must_change_password` tinyint(1) NOT NULL DEFAULT '0',
  `real_name` varchar(100) DEFAULT NULL,
  `gender` varchar(10) DEFAULT NULL,
  `birth_date` date DEFAULT NULL,
  `guardian_email` varchar(255) DEFAULT NULL,
  `interests` json DEFAULT NULL COMMENT '학생 관심사 목록. 예: ["수학", "과학", "영어"]',
  `onboarding_completed` tinyint(1) NOT NULL DEFAULT '0' COMMENT '학생 관심사 온보딩 완료 여부',
  `onboarding_completed_at` datetime DEFAULT NULL COMMENT '학생 관심사 온보딩 최초 완료 일시',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_student_profiles_student_code` (`student_code`),
  UNIQUE KEY `ix_student_profiles_student_login_id` (`student_login_id`),
  KEY `ix_student_profiles_class_id` (`class_id`),
  KEY `ix_student_profiles_organization_id` (`organization_id`),
  CONSTRAINT `student_profiles_ibfk_1` FOREIGN KEY (`class_id`) REFERENCES `classes` (`id`),
  CONSTRAINT `student_profiles_ibfk_2` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `student_progress`;
CREATE TABLE `student_progress` (
  `organization_id` char(36) DEFAULT NULL,
  `student_id` char(36) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `chapters_done` int NOT NULL,
  `current_chapter` int NOT NULL,
  `questions_done` int NOT NULL,
  `accuracy` float NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_progress_subject` (`student_id`,`subject`),
  KEY `ix_student_progress_organization_id` (`organization_id`),
  KEY `ix_student_progress_student_id` (`student_id`),
  KEY `ix_student_progress_subject` (`subject`),
  CONSTRAINT `student_progress_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `student_question_states`;
CREATE TABLE `student_question_states` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `question_id` varchar(80) NOT NULL,
  `subject` varchar(20) NOT NULL,
  `state` varchar(10) NOT NULL DEFAULT 'learning',
  `correct_streak` int NOT NULL DEFAULT '0',
  `wrong_count` int NOT NULL DEFAULT '0',
  `last_result` varchar(10) NOT NULL,
  `next_review_at` datetime DEFAULT NULL,
  `last_attempt_at` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sqs_student_question` (`student_id`,`question_id`),
  KEY `ix_student_question_states_student_id` (`student_id`),
  KEY `ix_sqs_student_subject` (`student_id`,`subject`),
  KEY `ix_student_question_states_next_review_at` (`next_review_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `subscriptions`;
CREATE TABLE `subscriptions` (
  `organization_id` char(36) NOT NULL,
  `plan_id` char(36) NOT NULL,
  `billing_cycle` varchar(10) NOT NULL,
  `status` varchar(20) NOT NULL,
  `auto_renew` tinyint(1) NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_subscriptions_organization_id` (`organization_id`),
  KEY `plan_id` (`plan_id`),
  CONSTRAINT `subscriptions_ibfk_1` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`),
  CONSTRAINT `subscriptions_ibfk_2` FOREIGN KEY (`plan_id`) REFERENCES `plans` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `system_health_logs`;
CREATE TABLE `system_health_logs` (
  `service_name` varchar(60) NOT NULL,
  `status` varchar(20) NOT NULL,
  `latency_ms` int NOT NULL,
  `checked_at` datetime DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `system_settings`;
CREATE TABLE `system_settings` (
  `id` char(36) NOT NULL,
  `key` varchar(60) NOT NULL,
  `value` text NOT NULL,
  `updated_by` char(36) DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_system_setting_key` (`key`),
  KEY `ix_system_settings_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `user_settings`;
CREATE TABLE `user_settings` (
  `subject_type` varchar(10) NOT NULL,
  `subject_id` char(36) NOT NULL,
  `settings` json NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_user_setting_subject` (`subject_type`,`subject_id`),
  KEY `ix_user_settings_subject_id` (`subject_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `email` varchar(255) DEFAULT NULL,
  `password_hash` varchar(255) NOT NULL,
  `name` varchar(100) NOT NULL,
  `phone` varchar(30) DEFAULT NULL,
  `role` varchar(20) NOT NULL,
  `status` varchar(20) NOT NULL,
  `email_verified_at` datetime DEFAULT NULL,
  `last_login_at` datetime DEFAULT NULL,
  `two_factor_enabled` tinyint(1) NOT NULL,
  `organization_id` char(36) DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `must_change_password` tinyint(1) NOT NULL DEFAULT '0',
  `password_reset_expires_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_email` (`email`),
  KEY `ix_users_organization_id` (`organization_id`),
  KEY `ix_users_role` (`role`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP TABLE IF EXISTS `wrong_answers`;
CREATE TABLE `wrong_answers` (
  `student_id` char(36) NOT NULL,
  `organization_id` char(36) DEFAULT NULL,
  `subject` varchar(20) NOT NULL,
  `category` varchar(30) NOT NULL,
  `question` text NOT NULL,
  `my_answer` varchar(200) NOT NULL,
  `correct_answer` varchar(200) NOT NULL,
  `tip` text,
  `reviewed` tinyint(1) NOT NULL,
  `wrong_date` date DEFAULT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `chapter_no` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_wrong_answers_organization_id` (`organization_id`),
  KEY `ix_wrong_answers_student_id` (`student_id`),
  KEY `ix_wrong_answers_subject` (`subject`),
  KEY `ix_wrong_chapter` (`chapter_no`),
  CONSTRAINT `wrong_answers_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 뷰 ai_training_dataset 는 권한이 없어 못 뜸: OperationalError

-- alembic_version: merge_heads_0807
