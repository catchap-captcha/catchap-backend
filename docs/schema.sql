
/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `alembic_version` (
  `version_num` varchar(32) NOT NULL,
  PRIMARY KEY (`version_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  PRIMARY KEY (`id`),
  KEY `ix_behavior_summaries_organization_id` (`organization_id`),
  KEY `ix_behavior_summaries_student_id` (`student_id`),
  KEY `ix_bs_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  KEY `ix_captcha_consumed_token_id` (`token_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `consents` (
  `id` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `student_id` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `organization_id` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `granted_by_user_id` char(36) COLLATE utf8mb4_unicode_ci NOT NULL,
  `consent_type` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'personal_info',
  `terms_version` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'v1',
  `granted_at` datetime NOT NULL,
  `withdrawn_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL,
  `updated_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_consent_student` (`student_id`),
  KEY `ix_consent_org` (`organization_id`),
  KEY `ix_consent_grantor` (`granted_by_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `learning_attempts` (
  `organization_id` char(36) NOT NULL,
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `lecture_checkpoint_events` (
  `id` char(36) NOT NULL,
  `student_id` char(36) NOT NULL,
  `lecture_id` char(36) NOT NULL,
  `position_sec` int NOT NULL DEFAULT '0',
  `result` varchar(20) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_lce_student_created` (`student_id`,`created_at`),
  KEY `ix_lce_lecture` (`lecture_id`),
  CONSTRAINT `lecture_checkpoint_events_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`),
  CONSTRAINT `lecture_checkpoint_events_ibfk_2` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  `pinned` tinyint(1) NOT NULL DEFAULT '0',
  `window_sec` int NOT NULL DEFAULT '0',
  `answer_indexes` json DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_lq_lecture` (`lecture_id`),
  KEY `ix_lq_lecture_pos` (`lecture_id`,`position_sec`),
  CONSTRAINT `lecture_questions_ibfk_1` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  `suspicion` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_lecture_watch` (`student_id`,`lecture_id`),
  KEY `ix_lwp_student` (`student_id`),
  KEY `ix_lwp_lecture` (`lecture_id`),
  CONSTRAINT `lecture_watch_progress_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student_profiles` (`id`),
  CONSTRAINT `lecture_watch_progress_ibfk_2` FOREIGN KEY (`lecture_id`) REFERENCES `lectures` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `lectures` (
  `id` char(36) NOT NULL,
  `title` varchar(200) NOT NULL,
  `description` text,
  `subject` varchar(20) NOT NULL,
  `video_ext` varchar(10) NOT NULL,
  `video_bytes` bigint NOT NULL DEFAULT '0',
  `duration_sec` int NOT NULL DEFAULT '0',
  `check_min_sec` int NOT NULL DEFAULT '60',
  `check_max_sec` int NOT NULL DEFAULT '180',
  `status` varchar(20) NOT NULL DEFAULT 'active',
  `uploaded_by` char(36) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `order_no` int NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `uploaded_by` (`uploaded_by`),
  KEY `ix_lecture_subject_status` (`subject`,`status`),
  KEY `ix_lecture_created` (`created_at`),
  CONSTRAINT `lectures_ibfk_1` FOREIGN KEY (`uploaded_by`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `login_throttle` (
  `identifier` varchar(255) NOT NULL,
  `fail_count` int NOT NULL,
  `id` char(36) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_login_throttle_identifier` (`identifier`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_student_profiles_student_code` (`student_code`),
  UNIQUE KEY `ix_student_profiles_student_login_id` (`student_login_id`),
  KEY `ix_student_profiles_class_id` (`class_id`),
  KEY `ix_student_profiles_organization_id` (`organization_id`),
  CONSTRAINT `student_profiles_ibfk_1` FOREIGN KEY (`class_id`) REFERENCES `classes` (`id`),
  CONSTRAINT `student_profiles_ibfk_2` FOREIGN KEY (`organization_id`) REFERENCES `organizations` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `student_progress` (
  `organization_id` char(36) NOT NULL,
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_email` (`email`),
  KEY `ix_users_organization_id` (`organization_id`),
  KEY `ix_users_role` (`role`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `wrong_answers` (
  `student_id` char(36) NOT NULL,
  `organization_id` char(36) NOT NULL,
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
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

