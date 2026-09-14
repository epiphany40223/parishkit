-- Initial unreleased stewardship schema. See docs/guides/stewardship-schema.md.

-- CONSTRAINT: stewardship_address_grant address_grant_role
ALTER TABLE ONLY public.stewardship_address_grant
    ADD CONSTRAINT address_grant_role UNIQUE (rule_id, role);

-- CONSTRAINT: stewardship_address_rule address_rule_identity
ALTER TABLE ONLY public.stewardship_address_rule
    ADD CONSTRAINT address_rule_identity UNIQUE (configuration_id, email);

-- CONSTRAINT: stewardship_address_rule addressrule_record
ALTER TABLE ONLY public.stewardship_address_rule
    ADD CONSTRAINT addressrule_record UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_auth_incident auth_incident_window
ALTER TABLE ONLY public.stewardship_auth_incident
    ADD CONSTRAINT auth_incident_window UNIQUE (kind, "window");

-- CONSTRAINT: stewardship_branding_asset branding_asset_variant
ALTER TABLE ONLY public.stewardship_branding_asset
    ADD CONSTRAINT branding_asset_variant UNIQUE (bundle_id, label);

-- CONSTRAINT: stewardship_campaign_boundary campaign_boundary_identity
ALTER TABLE ONLY public.stewardship_campaign_boundary
    ADD CONSTRAINT campaign_boundary_identity UNIQUE (campaign_id, kind, due_at);

-- CONSTRAINT: stewardship_family_code_mac campaign_code_key_unique
ALTER TABLE ONLY public.stewardship_family_code_mac
    ADD CONSTRAINT campaign_code_key_unique UNIQUE (campaign_id, key_id, digest);

-- CONSTRAINT: stewardship_campaign_control campaign_control_version
ALTER TABLE ONLY public.stewardship_campaign_control
    ADD CONSTRAINT campaign_control_version UNIQUE (campaign_id, expected_version);

-- CONSTRAINT: stewardship_campaign_mail_test campaign_mail_explicit_request
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT campaign_mail_explicit_request UNIQUE (requested_by_id, request_key);

-- CONSTRAINT: stewardship_campaign_configuration campaign_projection_identity
ALTER TABLE ONLY public.stewardship_campaign_configuration
    ADD CONSTRAINT campaign_projection_identity UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_campaign_configuration campaign_projection_name
ALTER TABLE ONLY public.stewardship_campaign_configuration
    ADD CONSTRAINT campaign_projection_name UNIQUE (configuration_id, name);

-- CONSTRAINT: stewardship_campaign_transition campaign_transition_version
ALTER TABLE ONLY public.stewardship_campaign_transition
    ADD CONSTRAINT campaign_transition_version UNIQUE (campaign_id, expected_version);

-- CONSTRAINT: stewardship_catchup_checkpoint catchup_checkpoint_group
ALTER TABLE ONLY public.stewardship_catchup_checkpoint
    ADD CONSTRAINT catchup_checkpoint_group UNIQUE (demand_id, group_key);

-- CONSTRAINT: stewardship_catchup_checkpoint catchup_checkpoint_sequence
ALTER TABLE ONLY public.stewardship_catchup_checkpoint
    ADD CONSTRAINT catchup_checkpoint_sequence UNIQUE (demand_id, sequence);

-- CONSTRAINT: stewardship_catchup_failure catchup_failure_version
ALTER TABLE ONLY public.stewardship_catchup_failure
    ADD CONSTRAINT catchup_failure_version UNIQUE (demand_id, expected_version);

-- CONSTRAINT: stewardship_chair_reconciliation chair_reconciliation_input
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT chair_reconciliation_input UNIQUE (configuration_id, snapshot_id);

-- CONSTRAINT: stewardship_config_checkpoint config_checkpoint_sequence
ALTER TABLE ONLY public.stewardship_config_checkpoint
    ADD CONSTRAINT config_checkpoint_sequence UNIQUE (request_id, sequence);

-- CONSTRAINT: stewardship_config_request config_request_actor_key
ALTER TABLE ONLY public.stewardship_config_request
    ADD CONSTRAINT config_request_actor_key UNIQUE (actor_id, request_key);

-- CONSTRAINT: stewardship_applied_integration configuration_integration_id
ALTER TABLE ONLY public.stewardship_applied_integration
    ADD CONSTRAINT configuration_integration_id UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_applied_integration configuration_integration_kind
ALTER TABLE ONLY public.stewardship_applied_integration
    ADD CONSTRAINT configuration_integration_kind UNIQUE (configuration_id, kind);

-- CONSTRAINT: stewardship_content_version content_revision_projection
ALTER TABLE ONLY public.stewardship_content_version
    ADD CONSTRAINT content_revision_projection UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_credential_consumer_ack credential_ack_consumer_once
ALTER TABLE ONLY public.stewardship_credential_consumer_ack
    ADD CONSTRAINT credential_ack_consumer_once UNIQUE (request_id, consumer);

-- CONSTRAINT: stewardship_daily_fact daily_fact_date
ALTER TABLE ONLY public.stewardship_daily_fact
    ADD CONSTRAINT daily_fact_date UNIQUE (fact_set_id, local_date);

-- CONSTRAINT: stewardship_daily_fact_set daily_fact_exact_inputs
ALTER TABLE ONLY public.stewardship_daily_fact_set
    ADD CONSTRAINT daily_fact_exact_inputs UNIQUE (campaign_id, population_scope, source_generation, submission_watermark, timezone_configuration_id, through_date);

-- CONSTRAINT: stewardship_domain_rule domain_rule_identity
ALTER TABLE ONLY public.stewardship_domain_rule
    ADD CONSTRAINT domain_rule_identity UNIQUE (configuration_id, domain);

-- CONSTRAINT: stewardship_domain_rule domainrule_record
ALTER TABLE ONLY public.stewardship_domain_rule
    ADD CONSTRAINT domainrule_record UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_fact_demand fact_demand_scope
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT fact_demand_scope UNIQUE (campaign_id, population_scope);

-- CONSTRAINT: stewardship_fact_pin fact_pin_parent
ALTER TABLE ONLY public.stewardship_fact_pin
    ADD CONSTRAINT fact_pin_parent UNIQUE (fact_set_id, parent_kind, parent_id);

-- CONSTRAINT: stewardship_fact_pointer fact_pointer_scope
ALTER TABLE ONLY public.stewardship_fact_pointer
    ADD CONSTRAINT fact_pointer_scope UNIQUE (campaign_id, population_scope);

-- CONSTRAINT: stewardship_family_campaign family_campaign_duid
ALTER TABLE ONLY public.stewardship_family_campaign
    ADD CONSTRAINT family_campaign_duid UNIQUE (campaign_id, family_duid);

-- CONSTRAINT: stewardship_family_code_mac family_code_key_once
ALTER TABLE ONLY public.stewardship_family_code_mac
    ADD CONSTRAINT family_code_key_once UNIQUE (family_id, key_id);

-- CONSTRAINT: stewardship_family_eligibility family_eligibility_version
ALTER TABLE ONLY public.stewardship_family_eligibility
    ADD CONSTRAINT family_eligibility_version UNIQUE (family_id, family_version);

-- CONSTRAINT: stewardship_family_token family_token_campaign_digest
ALTER TABLE ONLY public.stewardship_family_token
    ADD CONSTRAINT family_token_campaign_digest UNIQUE (campaign_id, digest);

-- CONSTRAINT: stewardship_family_token family_token_generation_once
ALTER TABLE ONLY public.stewardship_family_token
    ADD CONSTRAINT family_token_generation_once UNIQUE (family_id, generation_id);

-- CONSTRAINT: stewardship_ministry_activity ministry_activity_identity
ALTER TABLE ONLY public.stewardship_ministry_activity
    ADD CONSTRAINT ministry_activity_identity UNIQUE (configuration_id, organization_id, ministry_duid);

-- CONSTRAINT: stewardship_ministry_activity ministry_activity_record
ALTER TABLE ONLY public.stewardship_ministry_activity
    ADD CONSTRAINT ministry_activity_record UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_ministry_assignment ministry_assignment_identity
ALTER TABLE ONLY public.stewardship_ministry_assignment
    ADD CONSTRAINT ministry_assignment_identity UNIQUE (configuration_id, email, ministry_duid, source);

-- CONSTRAINT: stewardship_ministry_assignment ministryassignment_record
ALTER TABLE ONLY public.stewardship_ministry_assignment
    ADD CONSTRAINT ministryassignment_record UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_occurrence_transition occurrence_history_version
ALTER TABLE ONLY public.stewardship_occurrence_transition
    ADD CONSTRAINT occurrence_history_version UNIQUE (occurrence_id, version);

-- CONSTRAINT: stewardship_policy_security_event policy_security_event_identity
ALTER TABLE ONLY public.stewardship_policy_security_event
    ADD CONSTRAINT policy_security_event_identity UNIQUE (activation_id, rule_record_id);

-- CONSTRAINT: stewardship_postclose_resolution postclose_semantic_resolution
ALTER TABLE ONLY public.stewardship_postclose_resolution
    ADD CONSTRAINT postclose_semantic_resolution UNIQUE (campaign_id, mode, obligation_key, coverage_digest);

-- CONSTRAINT: stewardship_rehearsal_code_mac rehearsal_code_key_once
ALTER TABLE ONLY public.stewardship_rehearsal_code_mac
    ADD CONSTRAINT rehearsal_code_key_once UNIQUE (credential_id, key_id);

-- CONSTRAINT: stewardship_rehearsal_code_mac rehearsal_epoch_code_key_unique
ALTER TABLE ONLY public.stewardship_rehearsal_code_mac
    ADD CONSTRAINT rehearsal_epoch_code_key_unique UNIQUE (epoch_id, key_id, digest);

-- CONSTRAINT: stewardship_rehearsal_credential rehearsal_epoch_family_once
ALTER TABLE ONLY public.stewardship_rehearsal_credential
    ADD CONSTRAINT rehearsal_epoch_family_once UNIQUE (epoch_id, family_id);

-- CONSTRAINT: stewardship_rehearsal_credential rehearsal_epoch_token_unique
ALTER TABLE ONLY public.stewardship_rehearsal_credential
    ADD CONSTRAINT rehearsal_epoch_token_unique UNIQUE (epoch_id, token_digest);

-- CONSTRAINT: stewardship_rehearsal_reservation rehearsal_reservation_unique
ALTER TABLE ONLY public.stewardship_rehearsal_reservation
    ADD CONSTRAINT rehearsal_reservation_unique UNIQUE (campaign_id, key_id, algorithm, digest);

-- CONSTRAINT: stewardship_restore_hold_resolution restore_hold_resolution_version
ALTER TABLE ONLY public.stewardship_restore_hold_resolution
    ADD CONSTRAINT restore_hold_resolution_version UNIQUE (hold_id, version);

-- CONSTRAINT: stewardship_restore_delivery_hold restore_hold_semantic
ALTER TABLE ONLY public.stewardship_restore_delivery_hold
    ADD CONSTRAINT restore_hold_semantic UNIQUE (restore_id, definition_id, mode, target, slot);

-- CONSTRAINT: stewardship_schedule_fulfillment schedule_fulfillment_semantic
ALTER TABLE ONLY public.stewardship_schedule_fulfillment
    ADD CONSTRAINT schedule_fulfillment_semantic UNIQUE (definition_id, mode, target, slot);

-- CONSTRAINT: stewardship_schedule_occurrence schedule_occurrence_semantic_revision
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT schedule_occurrence_semantic_revision UNIQUE (revision_id, mode, target, slot);

-- CONSTRAINT: stewardship_schedule_revision schedule_revision_identity
ALTER TABLE ONLY public.stewardship_schedule_revision
    ADD CONSTRAINT schedule_revision_identity UNIQUE (configuration_id, record_id);

-- CONSTRAINT: stewardship_schedule_selection schedule_selection_configuration
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT schedule_selection_configuration UNIQUE (definition_id, configuration_id);

-- CONSTRAINT: stewardship_schedule_selection schedule_selection_version
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT schedule_selection_version UNIQUE (definition_id, version);

-- CONSTRAINT: stewardship_secret_checkpoint secret_checkpoint_sequence
ALTER TABLE ONLY public.stewardship_secret_checkpoint
    ADD CONSTRAINT secret_checkpoint_sequence UNIQUE (request_id, sequence);

-- CONSTRAINT: stewardship_setup_source_exchange setup_exchange_one_recipient_per_claim
ALTER TABLE ONLY public.stewardship_setup_source_exchange
    ADD CONSTRAINT setup_exchange_one_recipient_per_claim UNIQUE (task_id, task_fence, source_fence);

-- CONSTRAINT: stewardship_setup_credential_install setup_install_one_target
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT setup_install_one_target UNIQUE (readiness_id, target);

-- CONSTRAINT: stewardship_setup_mail_exchange setup_mail_exchange_one_recipient
ALTER TABLE ONLY public.stewardship_setup_mail_exchange
    ADD CONSTRAINT setup_mail_exchange_one_recipient UNIQUE (run_id, task_fence);

-- CONSTRAINT: stewardship_setup_mail_delivery setup_mail_explicit_request
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT setup_mail_explicit_request UNIQUE (attempt_id, request_key);

-- CONSTRAINT: stewardship_setup_draft_section setup_one_draft_per_step
ALTER TABLE ONLY public.stewardship_setup_draft_section
    ADD CONSTRAINT setup_one_draft_per_step UNIQUE (attempt_id, step);

-- CONSTRAINT: stewardship_setup_sealed_credential setup_secret_attempt_target
ALTER TABLE ONLY public.stewardship_setup_sealed_credential
    ADD CONSTRAINT setup_secret_attempt_target UNIQUE (attempt_id, target);

-- CONSTRAINT: stewardship_setup_slack_delivery setup_slack_explicit_request
ALTER TABLE ONLY public.stewardship_setup_slack_delivery
    ADD CONSTRAINT setup_slack_explicit_request UNIQUE (attempt_id, request_key);

-- CONSTRAINT: stewardship_snapshot_address snapshotaddress_identity
ALTER TABLE ONLY public.stewardship_snapshot_address
    ADD CONSTRAINT snapshotaddress_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_contact snapshotcontact_identity
ALTER TABLE ONLY public.stewardship_snapshot_contact
    ADD CONSTRAINT snapshotcontact_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_contribution snapshotcontribution_identity
ALTER TABLE ONLY public.stewardship_snapshot_contribution
    ADD CONSTRAINT snapshotcontribution_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_family snapshotfamily_identity
ALTER TABLE ONLY public.stewardship_snapshot_family
    ADD CONSTRAINT snapshotfamily_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_fund snapshotfund_identity
ALTER TABLE ONLY public.stewardship_snapshot_fund
    ADD CONSTRAINT snapshotfund_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_member snapshotmember_identity
ALTER TABLE ONLY public.stewardship_snapshot_member
    ADD CONSTRAINT snapshotmember_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_ministry snapshotministry_identity
ALTER TABLE ONLY public.stewardship_snapshot_ministry
    ADD CONSTRAINT snapshotministry_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_pledge snapshotpledge_identity
ALTER TABLE ONLY public.stewardship_snapshot_pledge
    ADD CONSTRAINT snapshotpledge_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_snapshot_roster snapshotroster_identity
ALTER TABLE ONLY public.stewardship_snapshot_roster
    ADD CONSTRAINT snapshotroster_identity UNIQUE (snapshot_id, source_key);

-- CONSTRAINT: stewardship_source_refresh_attempt source_attempt_claim
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT source_attempt_claim UNIQUE (task_id, task_fence);

-- CONSTRAINT: stewardship_source_pin source_pin_parent
ALTER TABLE ONLY public.stewardship_source_pin
    ADD CONSTRAINT source_pin_parent UNIQUE (snapshot_id, parent_kind, parent_id);

-- CONSTRAINT: stewardship_source_address sourceaddress_identity_digest
ALTER TABLE ONLY public.stewardship_source_address
    ADD CONSTRAINT sourceaddress_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_contact sourcecontact_identity_digest
ALTER TABLE ONLY public.stewardship_source_contact
    ADD CONSTRAINT sourcecontact_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_contribution sourcecontribution_identity_digest
ALTER TABLE ONLY public.stewardship_source_contribution
    ADD CONSTRAINT sourcecontribution_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_family sourcefamily_identity_digest
ALTER TABLE ONLY public.stewardship_source_family
    ADD CONSTRAINT sourcefamily_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_fund sourcefund_identity_digest
ALTER TABLE ONLY public.stewardship_source_fund
    ADD CONSTRAINT sourcefund_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_member sourcemember_identity_digest
ALTER TABLE ONLY public.stewardship_source_member
    ADD CONSTRAINT sourcemember_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_ministry sourceministry_identity_digest
ALTER TABLE ONLY public.stewardship_source_ministry
    ADD CONSTRAINT sourceministry_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_pledge sourcepledge_identity_digest
ALTER TABLE ONLY public.stewardship_source_pledge
    ADD CONSTRAINT sourcepledge_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_source_roster sourceroster_identity_digest
ALTER TABLE ONLY public.stewardship_source_roster
    ADD CONSTRAINT sourceroster_identity_digest UNIQUE (organization_id, source_key, digest);

-- CONSTRAINT: stewardship_activation_catchup stewardship_activation_catchup_activation_id_key
ALTER TABLE ONLY public.stewardship_activation_catchup
    ADD CONSTRAINT stewardship_activation_catchup_activation_id_key UNIQUE (activation_id);

-- CONSTRAINT: stewardship_activation_catchup stewardship_activation_catchup_pkey
ALTER TABLE ONLY public.stewardship_activation_catchup
    ADD CONSTRAINT stewardship_activation_catchup_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_address_grant stewardship_address_grant_pkey
ALTER TABLE ONLY public.stewardship_address_grant
    ADD CONSTRAINT stewardship_address_grant_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_address_rule stewardship_address_rule_pkey
ALTER TABLE ONLY public.stewardship_address_rule
    ADD CONSTRAINT stewardship_address_rule_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_admin_revocation stewardship_admin_revocation_activation_id_key
ALTER TABLE ONLY public.stewardship_admin_revocation
    ADD CONSTRAINT stewardship_admin_revocation_activation_id_key UNIQUE (activation_id);

-- CONSTRAINT: stewardship_admin_revocation stewardship_admin_revocation_pkey
ALTER TABLE ONLY public.stewardship_admin_revocation
    ADD CONSTRAINT stewardship_admin_revocation_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_applied_integration stewardship_applied_integration_pkey
ALTER TABLE ONLY public.stewardship_applied_integration
    ADD CONSTRAINT stewardship_applied_integration_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_assignment_overlay stewardship_assignment_overlay_assignment_record_id_key
ALTER TABLE ONLY public.stewardship_assignment_overlay
    ADD CONSTRAINT stewardship_assignment_overlay_assignment_record_id_key UNIQUE (assignment_record_id);

-- CONSTRAINT: stewardship_assignment_overlay stewardship_assignment_overlay_pkey
ALTER TABLE ONLY public.stewardship_assignment_overlay
    ADD CONSTRAINT stewardship_assignment_overlay_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_audit_context stewardship_audit_context_event_id_key
ALTER TABLE ONLY public.stewardship_audit_context
    ADD CONSTRAINT stewardship_audit_context_event_id_key UNIQUE (event_id);

-- CONSTRAINT: stewardship_audit_context stewardship_audit_context_pkey
ALTER TABLE ONLY public.stewardship_audit_context
    ADD CONSTRAINT stewardship_audit_context_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_audit_event stewardship_audit_event_pkey
ALTER TABLE ONLY public.stewardship_audit_event
    ADD CONSTRAINT stewardship_audit_event_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_auth_incident stewardship_auth_incident_pkey
ALTER TABLE ONLY public.stewardship_auth_incident
    ADD CONSTRAINT stewardship_auth_incident_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_branding_asset stewardship_branding_asset_pkey
ALTER TABLE ONLY public.stewardship_branding_asset
    ADD CONSTRAINT stewardship_branding_asset_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_branding_bundle stewardship_branding_bundle_pkey
ALTER TABLE ONLY public.stewardship_branding_bundle
    ADD CONSTRAINT stewardship_branding_bundle_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_boundary stewardship_campaign_boundary_pkey
ALTER TABLE ONLY public.stewardship_campaign_boundary
    ADD CONSTRAINT stewardship_campaign_boundary_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_boundary stewardship_campaign_boundary_transition_id_key
ALTER TABLE ONLY public.stewardship_campaign_boundary
    ADD CONSTRAINT stewardship_campaign_boundary_transition_id_key UNIQUE (transition_id);

-- CONSTRAINT: stewardship_campaign_config_abort stewardship_campaign_config_abort_intent_id_key
ALTER TABLE ONLY public.stewardship_campaign_config_abort
    ADD CONSTRAINT stewardship_campaign_config_abort_intent_id_key UNIQUE (intent_id);

-- CONSTRAINT: stewardship_campaign_config_abort stewardship_campaign_config_abort_pkey
ALTER TABLE ONLY public.stewardship_campaign_config_abort
    ADD CONSTRAINT stewardship_campaign_config_abort_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_config_intent stewardship_campaign_config_intent_pkey
ALTER TABLE ONLY public.stewardship_campaign_config_intent
    ADD CONSTRAINT stewardship_campaign_config_intent_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_config_intent stewardship_campaign_config_intent_request_id_key
ALTER TABLE ONLY public.stewardship_campaign_config_intent
    ADD CONSTRAINT stewardship_campaign_config_intent_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_campaign_configuration stewardship_campaign_configuration_pkey
ALTER TABLE ONLY public.stewardship_campaign_configuration
    ADD CONSTRAINT stewardship_campaign_configuration_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_control stewardship_campaign_control_pkey
ALTER TABLE ONLY public.stewardship_campaign_control
    ADD CONSTRAINT stewardship_campaign_control_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_control stewardship_campaign_control_request_id_key
ALTER TABLE ONLY public.stewardship_campaign_control
    ADD CONSTRAINT stewardship_campaign_control_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_campaign_credentials stewardship_campaign_credentials_campaign_id_key
ALTER TABLE ONLY public.stewardship_campaign_credentials
    ADD CONSTRAINT stewardship_campaign_credentials_campaign_id_key UNIQUE (campaign_id);

-- CONSTRAINT: stewardship_campaign_credentials stewardship_campaign_credentials_pkey
ALTER TABLE ONLY public.stewardship_campaign_credentials
    ADD CONSTRAINT stewardship_campaign_credentials_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_mail_test_pkey
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_mail_test_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_mail_test_task_id_key
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_mail_test_task_id_key UNIQUE (task_id);

-- CONSTRAINT: stewardship_campaign stewardship_campaign_pkey
ALTER TABLE ONLY public.stewardship_campaign
    ADD CONSTRAINT stewardship_campaign_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_transition stewardship_campaign_transition_pkey
ALTER TABLE ONLY public.stewardship_campaign_transition
    ADD CONSTRAINT stewardship_campaign_transition_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_transition stewardship_campaign_transition_request_id_key
ALTER TABLE ONLY public.stewardship_campaign_transition
    ADD CONSTRAINT stewardship_campaign_transition_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_campaign_work_gate stewardship_campaign_work_gate_pkey
ALTER TABLE ONLY public.stewardship_campaign_work_gate
    ADD CONSTRAINT stewardship_campaign_work_gate_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_campaign_work_gate stewardship_campaign_work_gate_request_id_key
ALTER TABLE ONLY public.stewardship_campaign_work_gate
    ADD CONSTRAINT stewardship_campaign_work_gate_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_catchup_checkpoint stewardship_catchup_checkpoint_pkey
ALTER TABLE ONLY public.stewardship_catchup_checkpoint
    ADD CONSTRAINT stewardship_catchup_checkpoint_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_catchup_failure stewardship_catchup_failure_pkey
ALTER TABLE ONLY public.stewardship_catchup_failure
    ADD CONSTRAINT stewardship_catchup_failure_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_chair_reconciliation stewardship_chair_reconciliation_activation_id_key
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT stewardship_chair_reconciliation_activation_id_key UNIQUE (activation_id);

-- CONSTRAINT: stewardship_chair_reconciliation stewardship_chair_reconciliation_pkey
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT stewardship_chair_reconciliation_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_chair_review stewardship_chair_review_pkey
ALTER TABLE ONLY public.stewardship_chair_review
    ADD CONSTRAINT stewardship_chair_review_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_chair_seed_evidence stewardship_chair_seed_evidence_assignment_id_key
ALTER TABLE ONLY public.stewardship_chair_seed_evidence
    ADD CONSTRAINT stewardship_chair_seed_evidence_assignment_id_key UNIQUE (assignment_id);

-- CONSTRAINT: stewardship_chair_seed_evidence stewardship_chair_seed_evidence_assignment_record_id_key
ALTER TABLE ONLY public.stewardship_chair_seed_evidence
    ADD CONSTRAINT stewardship_chair_seed_evidence_assignment_record_id_key UNIQUE (assignment_record_id);

-- CONSTRAINT: stewardship_chair_seed_evidence stewardship_chair_seed_evidence_pkey
ALTER TABLE ONLY public.stewardship_chair_seed_evidence
    ADD CONSTRAINT stewardship_chair_seed_evidence_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_config_activation stewardship_config_activation_configuration_id_key
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_activation_configuration_id_key UNIQUE (configuration_id);

-- CONSTRAINT: stewardship_config_activation stewardship_config_activation_pkey
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_activation_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_config_activation stewardship_config_activation_request_id_key
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_activation_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_config_activation stewardship_config_activation_sequence_key
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_activation_sequence_key UNIQUE (sequence);

-- CONSTRAINT: stewardship_config_checkpoint stewardship_config_checkpoint_pkey
ALTER TABLE ONLY public.stewardship_config_checkpoint
    ADD CONSTRAINT stewardship_config_checkpoint_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_config_request stewardship_config_request_candidate_digest_key
ALTER TABLE ONLY public.stewardship_config_request
    ADD CONSTRAINT stewardship_config_request_candidate_digest_key UNIQUE (candidate_digest);

-- CONSTRAINT: stewardship_config_request stewardship_config_request_candidate_version_id_key
ALTER TABLE ONLY public.stewardship_config_request
    ADD CONSTRAINT stewardship_config_request_candidate_version_id_key UNIQUE (candidate_version_id);

-- CONSTRAINT: stewardship_config_request stewardship_config_request_pkey
ALTER TABLE ONLY public.stewardship_config_request
    ADD CONSTRAINT stewardship_config_request_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_configuration_version stewardship_configuration_version_digest_key
ALTER TABLE ONLY public.stewardship_configuration_version
    ADD CONSTRAINT stewardship_configuration_version_digest_key UNIQUE (digest);

-- CONSTRAINT: stewardship_configuration_version stewardship_configuration_version_pkey
ALTER TABLE ONLY public.stewardship_configuration_version
    ADD CONSTRAINT stewardship_configuration_version_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_content_version stewardship_content_version_pkey
ALTER TABLE ONLY public.stewardship_content_version
    ADD CONSTRAINT stewardship_content_version_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_credential_consumer_ack stewardship_credential_consumer_ack_pkey
ALTER TABLE ONLY public.stewardship_credential_consumer_ack
    ADD CONSTRAINT stewardship_credential_consumer_ack_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_credential_deployment stewardship_credential_deployment_pkey
ALTER TABLE ONLY public.stewardship_credential_deployment
    ADD CONSTRAINT stewardship_credential_deployment_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_credential_key_state stewardship_credential_key_state_kind_key
ALTER TABLE ONLY public.stewardship_credential_key_state
    ADD CONSTRAINT stewardship_credential_key_state_kind_key UNIQUE (kind);

-- CONSTRAINT: stewardship_credential_key_state stewardship_credential_key_state_pkey
ALTER TABLE ONLY public.stewardship_credential_key_state
    ADD CONSTRAINT stewardship_credential_key_state_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_daily_fact stewardship_daily_fact_pkey
ALTER TABLE ONLY public.stewardship_daily_fact
    ADD CONSTRAINT stewardship_daily_fact_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_daily_fact_set stewardship_daily_fact_set_pkey
ALTER TABLE ONLY public.stewardship_daily_fact_set
    ADD CONSTRAINT stewardship_daily_fact_set_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_domain_rule stewardship_domain_rule_pkey
ALTER TABLE ONLY public.stewardship_domain_rule
    ADD CONSTRAINT stewardship_domain_rule_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_download_policy stewardship_download_policy_pkey
ALTER TABLE ONLY public.stewardship_download_policy
    ADD CONSTRAINT stewardship_download_policy_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_fact_compaction stewardship_fact_compaction_fact_set_id_key
ALTER TABLE ONLY public.stewardship_fact_compaction
    ADD CONSTRAINT stewardship_fact_compaction_fact_set_id_key UNIQUE (fact_set_id);

-- CONSTRAINT: stewardship_fact_compaction stewardship_fact_compaction_pkey
ALTER TABLE ONLY public.stewardship_fact_compaction
    ADD CONSTRAINT stewardship_fact_compaction_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_fact_demand stewardship_fact_demand_pkey
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT stewardship_fact_demand_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_fact_pin stewardship_fact_pin_pkey
ALTER TABLE ONLY public.stewardship_fact_pin
    ADD CONSTRAINT stewardship_fact_pin_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_fact_pointer stewardship_fact_pointer_pkey
ALTER TABLE ONLY public.stewardship_fact_pointer
    ADD CONSTRAINT stewardship_fact_pointer_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_family_campaign stewardship_family_campaign_pkey
ALTER TABLE ONLY public.stewardship_family_campaign
    ADD CONSTRAINT stewardship_family_campaign_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_family_code_mac stewardship_family_code_mac_pkey
ALTER TABLE ONLY public.stewardship_family_code_mac
    ADD CONSTRAINT stewardship_family_code_mac_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_family_eligibility stewardship_family_eligibility_pkey
ALTER TABLE ONLY public.stewardship_family_eligibility
    ADD CONSTRAINT stewardship_family_eligibility_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_family_session stewardship_family_session_pkey
ALTER TABLE ONLY public.stewardship_family_session
    ADD CONSTRAINT stewardship_family_session_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_family_session stewardship_family_session_session_id_key
ALTER TABLE ONLY public.stewardship_family_session
    ADD CONSTRAINT stewardship_family_session_session_id_key UNIQUE (session_id);

-- CONSTRAINT: stewardship_family_token_generation stewardship_family_token_generation_operation_id_key
ALTER TABLE ONLY public.stewardship_family_token_generation
    ADD CONSTRAINT stewardship_family_token_generation_operation_id_key UNIQUE (operation_id);

-- CONSTRAINT: stewardship_family_token_generation stewardship_family_token_generation_pkey
ALTER TABLE ONLY public.stewardship_family_token_generation
    ADD CONSTRAINT stewardship_family_token_generation_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_family_token stewardship_family_token_pkey
ALTER TABLE ONLY public.stewardship_family_token
    ADD CONSTRAINT stewardship_family_token_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_limiter_health stewardship_limiter_health_namespace_fingerprint_key
ALTER TABLE ONLY public.stewardship_limiter_health
    ADD CONSTRAINT stewardship_limiter_health_namespace_fingerprint_key UNIQUE (namespace_fingerprint);

-- CONSTRAINT: stewardship_limiter_health stewardship_limiter_health_pkey
ALTER TABLE ONLY public.stewardship_limiter_health
    ADD CONSTRAINT stewardship_limiter_health_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_ministry_activity stewardship_ministry_activity_pkey
ALTER TABLE ONLY public.stewardship_ministry_activity
    ADD CONSTRAINT stewardship_ministry_activity_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_ministry_assignment stewardship_ministry_assignment_pkey
ALTER TABLE ONLY public.stewardship_ministry_assignment
    ADD CONSTRAINT stewardship_ministry_assignment_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_oauth_consumption stewardship_oauth_consumption_pkey
ALTER TABLE ONLY public.stewardship_oauth_consumption
    ADD CONSTRAINT stewardship_oauth_consumption_pkey PRIMARY KEY (fingerprint);

-- CONSTRAINT: stewardship_occurrence_transition stewardship_occurrence_transition_pkey
ALTER TABLE ONLY public.stewardship_occurrence_transition
    ADD CONSTRAINT stewardship_occurrence_transition_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_operational_log stewardship_operational_log_pkey
ALTER TABLE ONLY public.stewardship_operational_log
    ADD CONSTRAINT stewardship_operational_log_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_parish stewardship_parish_configuration_id_key
ALTER TABLE ONLY public.stewardship_parish
    ADD CONSTRAINT stewardship_parish_configuration_id_key UNIQUE (configuration_id);

-- CONSTRAINT: stewardship_parish stewardship_parish_pkey
ALTER TABLE ONLY public.stewardship_parish
    ADD CONSTRAINT stewardship_parish_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_policy_epoch stewardship_policy_epoch_activation_id_key
ALTER TABLE ONLY public.stewardship_policy_epoch
    ADD CONSTRAINT stewardship_policy_epoch_activation_id_key UNIQUE (activation_id);

-- CONSTRAINT: stewardship_policy_epoch stewardship_policy_epoch_pkey
ALTER TABLE ONLY public.stewardship_policy_epoch
    ADD CONSTRAINT stewardship_policy_epoch_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_policy_epoch stewardship_policy_epoch_sequence_key
ALTER TABLE ONLY public.stewardship_policy_epoch
    ADD CONSTRAINT stewardship_policy_epoch_sequence_key UNIQUE (sequence);

-- CONSTRAINT: stewardship_policy_security_event stewardship_policy_security_event_pkey
ALTER TABLE ONLY public.stewardship_policy_security_event
    ADD CONSTRAINT stewardship_policy_security_event_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_portal_session stewardship_portal_session_pkey
ALTER TABLE ONLY public.stewardship_portal_session
    ADD CONSTRAINT stewardship_portal_session_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_portal_session stewardship_portal_session_session_id_key
ALTER TABLE ONLY public.stewardship_portal_session
    ADD CONSTRAINT stewardship_portal_session_session_id_key UNIQUE (session_id);

-- CONSTRAINT: stewardship_portal_user stewardship_portal_user_google_subject_key
ALTER TABLE ONLY public.stewardship_portal_user
    ADD CONSTRAINT stewardship_portal_user_google_subject_key UNIQUE (google_subject);

-- CONSTRAINT: stewardship_portal_user stewardship_portal_user_pkey
ALTER TABLE ONLY public.stewardship_portal_user
    ADD CONSTRAINT stewardship_portal_user_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_postclose_resolution stewardship_postclose_resolution_pkey
ALTER TABLE ONLY public.stewardship_postclose_resolution
    ADD CONSTRAINT stewardship_postclose_resolution_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_provider_context stewardship_provider_context_pkey
ALTER TABLE ONLY public.stewardship_provider_context
    ADD CONSTRAINT stewardship_provider_context_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_provider_context stewardship_provider_context_request_id_key
ALTER TABLE ONLY public.stewardship_provider_context
    ADD CONSTRAINT stewardship_provider_context_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_public_credential_handoff stewardship_public_credential_handoff_pkey
ALTER TABLE ONLY public.stewardship_public_credential_handoff
    ADD CONSTRAINT stewardship_public_credential_handoff_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_public_credential_handoff stewardship_public_credential_handoff_public_key_key
ALTER TABLE ONLY public.stewardship_public_credential_handoff
    ADD CONSTRAINT stewardship_public_credential_handoff_public_key_key UNIQUE (public_key);

-- CONSTRAINT: stewardship_public_credential_handoff stewardship_public_credential_handoff_target_key
ALTER TABLE ONLY public.stewardship_public_credential_handoff
    ADD CONSTRAINT stewardship_public_credential_handoff_target_key UNIQUE (target);

-- CONSTRAINT: stewardship_rehearsal_code_mac stewardship_rehearsal_code_mac_pkey
ALTER TABLE ONLY public.stewardship_rehearsal_code_mac
    ADD CONSTRAINT stewardship_rehearsal_code_mac_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_rehearsal_credential stewardship_rehearsal_credential_pkey
ALTER TABLE ONLY public.stewardship_rehearsal_credential
    ADD CONSTRAINT stewardship_rehearsal_credential_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_rehearsal_epoch stewardship_rehearsal_epoch_pkey
ALTER TABLE ONLY public.stewardship_rehearsal_epoch
    ADD CONSTRAINT stewardship_rehearsal_epoch_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_rehearsal_reservation stewardship_rehearsal_reservation_pkey
ALTER TABLE ONLY public.stewardship_rehearsal_reservation
    ADD CONSTRAINT stewardship_rehearsal_reservation_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_restore_delivery_hold stewardship_restore_delivery_hold_pkey
ALTER TABLE ONLY public.stewardship_restore_delivery_hold
    ADD CONSTRAINT stewardship_restore_delivery_hold_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_restore_hold_resolution stewardship_restore_hold_resolution_pkey
ALTER TABLE ONLY public.stewardship_restore_hold_resolution
    ADD CONSTRAINT stewardship_restore_hold_resolution_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_runtime_transition stewardship_runtime_transition_campaign_transition_id_key
ALTER TABLE ONLY public.stewardship_runtime_transition
    ADD CONSTRAINT stewardship_runtime_transition_campaign_transition_id_key UNIQUE (campaign_transition_id);

-- CONSTRAINT: stewardship_runtime_transition stewardship_runtime_transition_expected_version_key
ALTER TABLE ONLY public.stewardship_runtime_transition
    ADD CONSTRAINT stewardship_runtime_transition_expected_version_key UNIQUE (expected_version);

-- CONSTRAINT: stewardship_runtime_transition stewardship_runtime_transition_pkey
ALTER TABLE ONLY public.stewardship_runtime_transition
    ADD CONSTRAINT stewardship_runtime_transition_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_runtime_transition stewardship_runtime_transition_request_id_key
ALTER TABLE ONLY public.stewardship_runtime_transition
    ADD CONSTRAINT stewardship_runtime_transition_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_schedule_definition stewardship_schedule_definition_pkey
ALTER TABLE ONLY public.stewardship_schedule_definition
    ADD CONSTRAINT stewardship_schedule_definition_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_schedule_fulfillment stewardship_schedule_fulfillment_pkey
ALTER TABLE ONLY public.stewardship_schedule_fulfillment
    ADD CONSTRAINT stewardship_schedule_fulfillment_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_schedule_occurrence stewardship_schedule_occurrence_occurrence_key_key
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT stewardship_schedule_occurrence_occurrence_key_key UNIQUE (occurrence_key);

-- CONSTRAINT: stewardship_schedule_occurrence stewardship_schedule_occurrence_pkey
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT stewardship_schedule_occurrence_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_schedule_revision stewardship_schedule_revision_pkey
ALTER TABLE ONLY public.stewardship_schedule_revision
    ADD CONSTRAINT stewardship_schedule_revision_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_schedule_selection stewardship_schedule_selection_pkey
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT stewardship_schedule_selection_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_sealed_credential_staging stewardship_sealed_credential_staging_pkey
ALTER TABLE ONLY public.stewardship_sealed_credential_staging
    ADD CONSTRAINT stewardship_sealed_credential_staging_pkey PRIMARY KEY (reference);

-- CONSTRAINT: stewardship_sealed_credential_staging stewardship_sealed_credential_staging_request_id_key
ALTER TABLE ONLY public.stewardship_sealed_credential_staging
    ADD CONSTRAINT stewardship_sealed_credential_staging_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_secret_checkpoint stewardship_secret_checkpoint_pkey
ALTER TABLE ONLY public.stewardship_secret_checkpoint
    ADD CONSTRAINT stewardship_secret_checkpoint_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_secret_request stewardship_secret_request_pkey
ALTER TABLE ONLY public.stewardship_secret_request
    ADD CONSTRAINT stewardship_secret_request_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_secret_request stewardship_secret_request_staging_reference_key
ALTER TABLE ONLY public.stewardship_secret_request
    ADD CONSTRAINT stewardship_secret_request_staging_reference_key UNIQUE (staging_reference);

-- CONSTRAINT: stewardship_setup_attempt stewardship_setup_attempt_pkey
ALTER TABLE ONLY public.stewardship_setup_attempt
    ADD CONSTRAINT stewardship_setup_attempt_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_attempt stewardship_setup_attempt_session_id_key
ALTER TABLE ONLY public.stewardship_setup_attempt
    ADD CONSTRAINT stewardship_setup_attempt_session_id_key UNIQUE (session_id);

-- CONSTRAINT: stewardship_setup_attempt stewardship_setup_attempt_source_task_id_key
ALTER TABLE ONLY public.stewardship_setup_attempt
    ADD CONSTRAINT stewardship_setup_attempt_source_task_id_key UNIQUE (source_task_id);

-- CONSTRAINT: stewardship_setup_completion stewardship_setup_completion_activation_id_key
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_completion_activation_id_key UNIQUE (activation_id);

-- CONSTRAINT: stewardship_setup_completion stewardship_setup_completion_pkey
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_completion_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_completion stewardship_setup_completion_preparation_id_key
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_completion_preparation_id_key UNIQUE (preparation_id);

-- CONSTRAINT: stewardship_setup_completion stewardship_setup_completion_snapshot_id_key
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_completion_snapshot_id_key UNIQUE (snapshot_id);

-- CONSTRAINT: stewardship_setup_completion stewardship_setup_completion_task_id_key
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_completion_task_id_key UNIQUE (task_id);

-- CONSTRAINT: stewardship_setup_config_abort stewardship_setup_config_abort_intent_id_key
ALTER TABLE ONLY public.stewardship_setup_config_abort
    ADD CONSTRAINT stewardship_setup_config_abort_intent_id_key UNIQUE (intent_id);

-- CONSTRAINT: stewardship_setup_config_abort stewardship_setup_config_abort_pkey
ALTER TABLE ONLY public.stewardship_setup_config_abort
    ADD CONSTRAINT stewardship_setup_config_abort_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_config_intent stewardship_setup_config_intent_attempt_id_key
ALTER TABLE ONLY public.stewardship_setup_config_intent
    ADD CONSTRAINT stewardship_setup_config_intent_attempt_id_key UNIQUE (attempt_id);

-- CONSTRAINT: stewardship_setup_config_intent stewardship_setup_config_intent_pkey
ALTER TABLE ONLY public.stewardship_setup_config_intent
    ADD CONSTRAINT stewardship_setup_config_intent_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_config_intent stewardship_setup_config_intent_request_id_key
ALTER TABLE ONLY public.stewardship_setup_config_intent
    ADD CONSTRAINT stewardship_setup_config_intent_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_setup_credential_install stewardship_setup_credential_install_credential_id_key
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT stewardship_setup_credential_install_credential_id_key UNIQUE (credential_id);

-- CONSTRAINT: stewardship_setup_credential_install stewardship_setup_credential_install_pkey
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT stewardship_setup_credential_install_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_credential_install stewardship_setup_credential_install_request_id_key
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT stewardship_setup_credential_install_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_setup_draft_section stewardship_setup_draft_section_pkey
ALTER TABLE ONLY public.stewardship_setup_draft_section
    ADD CONSTRAINT stewardship_setup_draft_section_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_mail_delivery stewardship_setup_mail_delivery_pkey
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT stewardship_setup_mail_delivery_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_mail_delivery stewardship_setup_mail_delivery_task_id_key
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT stewardship_setup_mail_delivery_task_id_key UNIQUE (task_id);

-- CONSTRAINT: stewardship_setup_mail_exchange stewardship_setup_mail_exchange_pkey
ALTER TABLE ONLY public.stewardship_setup_mail_exchange
    ADD CONSTRAINT stewardship_setup_mail_exchange_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_prepared stewardship_setup_prepared_configuration_id_key
ALTER TABLE ONLY public.stewardship_setup_prepared
    ADD CONSTRAINT stewardship_setup_prepared_configuration_id_key UNIQUE (configuration_id);

-- CONSTRAINT: stewardship_setup_prepared stewardship_setup_prepared_pkey
ALTER TABLE ONLY public.stewardship_setup_prepared
    ADD CONSTRAINT stewardship_setup_prepared_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_prepared stewardship_setup_prepared_readiness_id_key
ALTER TABLE ONLY public.stewardship_setup_prepared
    ADD CONSTRAINT stewardship_setup_prepared_readiness_id_key UNIQUE (readiness_id);

-- CONSTRAINT: stewardship_setup_readiness_binding stewardship_setup_readiness_binding_intent_id_key
ALTER TABLE ONLY public.stewardship_setup_readiness_binding
    ADD CONSTRAINT stewardship_setup_readiness_binding_intent_id_key UNIQUE (intent_id);

-- CONSTRAINT: stewardship_setup_readiness_binding stewardship_setup_readiness_binding_pkey
ALTER TABLE ONLY public.stewardship_setup_readiness_binding
    ADD CONSTRAINT stewardship_setup_readiness_binding_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_sealed_credential stewardship_setup_sealed_credential_pkey
ALTER TABLE ONLY public.stewardship_setup_sealed_credential
    ADD CONSTRAINT stewardship_setup_sealed_credential_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_slack_delivery stewardship_setup_slack_delivery_pkey
ALTER TABLE ONLY public.stewardship_setup_slack_delivery
    ADD CONSTRAINT stewardship_setup_slack_delivery_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_source_exchange stewardship_setup_source_exchange_pkey
ALTER TABLE ONLY public.stewardship_setup_source_exchange
    ADD CONSTRAINT stewardship_setup_source_exchange_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_source_result stewardship_setup_source_result_exchange_id_key
ALTER TABLE ONLY public.stewardship_setup_source_result
    ADD CONSTRAINT stewardship_setup_source_result_exchange_id_key UNIQUE (exchange_id);

-- CONSTRAINT: stewardship_setup_source_result stewardship_setup_source_result_pkey
ALTER TABLE ONLY public.stewardship_setup_source_result
    ADD CONSTRAINT stewardship_setup_source_result_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_setup_source_result stewardship_setup_source_result_snapshot_id_key
ALTER TABLE ONLY public.stewardship_setup_source_result
    ADD CONSTRAINT stewardship_setup_source_result_snapshot_id_key UNIQUE (snapshot_id);

-- CONSTRAINT: stewardship_snapshot_address stewardship_snapshot_address_pkey
ALTER TABLE ONLY public.stewardship_snapshot_address
    ADD CONSTRAINT stewardship_snapshot_address_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_contact stewardship_snapshot_contact_pkey
ALTER TABLE ONLY public.stewardship_snapshot_contact
    ADD CONSTRAINT stewardship_snapshot_contact_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_contribution stewardship_snapshot_contribution_pkey
ALTER TABLE ONLY public.stewardship_snapshot_contribution
    ADD CONSTRAINT stewardship_snapshot_contribution_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_family stewardship_snapshot_family_pkey
ALTER TABLE ONLY public.stewardship_snapshot_family
    ADD CONSTRAINT stewardship_snapshot_family_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_fund stewardship_snapshot_fund_pkey
ALTER TABLE ONLY public.stewardship_snapshot_fund
    ADD CONSTRAINT stewardship_snapshot_fund_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_member stewardship_snapshot_member_pkey
ALTER TABLE ONLY public.stewardship_snapshot_member
    ADD CONSTRAINT stewardship_snapshot_member_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_ministry stewardship_snapshot_ministry_pkey
ALTER TABLE ONLY public.stewardship_snapshot_ministry
    ADD CONSTRAINT stewardship_snapshot_ministry_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_pledge stewardship_snapshot_pledge_pkey
ALTER TABLE ONLY public.stewardship_snapshot_pledge
    ADD CONSTRAINT stewardship_snapshot_pledge_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_snapshot_roster stewardship_snapshot_roster_pkey
ALTER TABLE ONLY public.stewardship_snapshot_roster
    ADD CONSTRAINT stewardship_snapshot_roster_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_address stewardship_source_address_pkey
ALTER TABLE ONLY public.stewardship_source_address
    ADD CONSTRAINT stewardship_source_address_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_compaction stewardship_source_compaction_pkey
ALTER TABLE ONLY public.stewardship_source_compaction
    ADD CONSTRAINT stewardship_source_compaction_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_contact stewardship_source_contact_pkey
ALTER TABLE ONLY public.stewardship_source_contact
    ADD CONSTRAINT stewardship_source_contact_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_contribution stewardship_source_contribution_pkey
ALTER TABLE ONLY public.stewardship_source_contribution
    ADD CONSTRAINT stewardship_source_contribution_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_current stewardship_source_current_pkey
ALTER TABLE ONLY public.stewardship_source_current
    ADD CONSTRAINT stewardship_source_current_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_current stewardship_source_current_singleton_key
ALTER TABLE ONLY public.stewardship_source_current
    ADD CONSTRAINT stewardship_source_current_singleton_key UNIQUE (singleton);

-- CONSTRAINT: stewardship_source_current stewardship_source_current_snapshot_id_key
ALTER TABLE ONLY public.stewardship_source_current
    ADD CONSTRAINT stewardship_source_current_snapshot_id_key UNIQUE (snapshot_id);

-- CONSTRAINT: stewardship_source_family stewardship_source_family_pkey
ALTER TABLE ONLY public.stewardship_source_family
    ADD CONSTRAINT stewardship_source_family_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_fund stewardship_source_fund_pkey
ALTER TABLE ONLY public.stewardship_source_fund
    ADD CONSTRAINT stewardship_source_fund_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_lease stewardship_source_lease_pkey
ALTER TABLE ONLY public.stewardship_source_lease
    ADD CONSTRAINT stewardship_source_lease_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_lease stewardship_source_lease_singleton_key
ALTER TABLE ONLY public.stewardship_source_lease
    ADD CONSTRAINT stewardship_source_lease_singleton_key UNIQUE (singleton);

-- CONSTRAINT: stewardship_source_member stewardship_source_member_pkey
ALTER TABLE ONLY public.stewardship_source_member
    ADD CONSTRAINT stewardship_source_member_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_ministry stewardship_source_ministry_pkey
ALTER TABLE ONLY public.stewardship_source_ministry
    ADD CONSTRAINT stewardship_source_ministry_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_pin stewardship_source_pin_pkey
ALTER TABLE ONLY public.stewardship_source_pin
    ADD CONSTRAINT stewardship_source_pin_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_pledge stewardship_source_pledge_pkey
ALTER TABLE ONLY public.stewardship_source_pledge
    ADD CONSTRAINT stewardship_source_pledge_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_refresh_attempt stewardship_source_refresh_attempt_pkey
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT stewardship_source_refresh_attempt_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_refresh_attempt stewardship_source_refresh_attempt_snapshot_id_key
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT stewardship_source_refresh_attempt_snapshot_id_key UNIQUE (snapshot_id);

-- CONSTRAINT: stewardship_source_refresh_command stewardship_source_refresh_command_pkey
ALTER TABLE ONLY public.stewardship_source_refresh_command
    ADD CONSTRAINT stewardship_source_refresh_command_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_refresh_fallback_command_id_key
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_refresh_fallback_command_id_key UNIQUE (command_id);

-- CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_refresh_fallback_pkey
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_refresh_fallback_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_refresh_fallback_request_id_key
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_refresh_fallback_request_id_key UNIQUE (request_id);

-- CONSTRAINT: stewardship_source_refresh_request stewardship_source_refresh_request_pkey
ALTER TABLE ONLY public.stewardship_source_refresh_request
    ADD CONSTRAINT stewardship_source_refresh_request_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_refresh_request stewardship_source_refresh_request_task_root_id_key
ALTER TABLE ONLY public.stewardship_source_refresh_request
    ADD CONSTRAINT stewardship_source_refresh_request_task_root_id_key UNIQUE (task_root_id);

-- CONSTRAINT: stewardship_source_refresh_tick stewardship_source_refresh_tick_command_id_key
ALTER TABLE ONLY public.stewardship_source_refresh_tick
    ADD CONSTRAINT stewardship_source_refresh_tick_command_id_key UNIQUE (command_id);

-- CONSTRAINT: stewardship_source_refresh_tick stewardship_source_refresh_tick_pkey
ALTER TABLE ONLY public.stewardship_source_refresh_tick
    ADD CONSTRAINT stewardship_source_refresh_tick_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_refresh_tick stewardship_source_refresh_tick_slot_key_key
ALTER TABLE ONLY public.stewardship_source_refresh_tick
    ADD CONSTRAINT stewardship_source_refresh_tick_slot_key_key UNIQUE (slot_key);

-- CONSTRAINT: stewardship_source_roster stewardship_source_roster_pkey
ALTER TABLE ONLY public.stewardship_source_roster
    ADD CONSTRAINT stewardship_source_roster_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_source_snapshot stewardship_source_snapshot_generation_key
ALTER TABLE ONLY public.stewardship_source_snapshot
    ADD CONSTRAINT stewardship_source_snapshot_generation_key UNIQUE (generation);

-- CONSTRAINT: stewardship_source_snapshot stewardship_source_snapshot_pkey
ALTER TABLE ONLY public.stewardship_source_snapshot
    ADD CONSTRAINT stewardship_source_snapshot_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_system_configuration stewardship_system_configuration_pkey
ALTER TABLE ONLY public.stewardship_system_configuration
    ADD CONSTRAINT stewardship_system_configuration_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_task_event stewardship_task_event_pkey
ALTER TABLE ONLY public.stewardship_task_event
    ADD CONSTRAINT stewardship_task_event_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_task_run stewardship_task_run_pkey
ALTER TABLE ONLY public.stewardship_task_run
    ADD CONSTRAINT stewardship_task_run_pkey PRIMARY KEY (id);

-- CONSTRAINT: stewardship_task_event task_event_version
ALTER TABLE ONLY public.stewardship_task_event
    ADD CONSTRAINT task_event_version UNIQUE (run_id, version);

-- CONSTRAINT: stewardship_task_run task_retry_sequence
ALTER TABLE ONLY public.stewardship_task_run
    ADD CONSTRAINT task_retry_sequence UNIQUE (root_id, retry_sequence);

-- INDEX: audit_event_time
CREATE INDEX audit_event_time ON public.stewardship_audit_event USING btree (event_type, created_at);

-- INDEX: auth_single_limiter_outage
CREATE UNIQUE INDEX auth_single_limiter_outage ON public.stewardship_auth_incident USING btree (kind) WHERE (((kind)::text = 'limiter_unavailable'::text) AND (resolved_at IS NULL));

-- INDEX: campaign_boundary_due
CREATE INDEX campaign_boundary_due ON public.stewardship_campaign_boundary USING btree (state, due_at);

-- INDEX: campaign_catchup_hold
CREATE INDEX campaign_catchup_hold ON public.stewardship_activation_catchup USING btree (campaign_id, completed_at);

-- INDEX: campaign_mail_one_pending
CREATE UNIQUE INDEX campaign_mail_one_pending ON public.stewardship_campaign_mail_test USING btree (campaign_id) WHERE ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]));

-- INDEX: campaign_one_current_state
CREATE UNIQUE INDEX campaign_one_current_state ON public.stewardship_campaign USING btree ((1)) WHERE ((state)::text = ANY ((ARRAY['draft'::character varying, 'scheduled'::character varying, 'active'::character varying, 'closed'::character varying])::text[]));

-- INDEX: campaign_one_rehearsal_epoch
CREATE UNIQUE INDEX campaign_one_rehearsal_epoch ON public.stewardship_rehearsal_epoch USING btree (campaign_id) WHERE ((state)::text = 'active'::text);

-- INDEX: campaign_one_work_gate
CREATE UNIQUE INDEX campaign_one_work_gate ON public.stewardship_campaign_work_gate USING btree (campaign_id) WHERE ((state)::text = ANY ((ARRAY['preparing'::character varying, 'running'::character varying])::text[]));

-- INDEX: chair_review_open_assignment
CREATE UNIQUE INDEX chair_review_open_assignment ON public.stewardship_chair_review USING btree (assignment_record_id) WHERE (closed_by_id IS NULL);

-- INDEX: config_request_patch_lookup
CREATE INDEX config_request_patch_lookup ON public.stewardship_config_request USING gin (patch jsonb_path_ops);

-- INDEX: configuration_single_root
CREATE UNIQUE INDEX configuration_single_root ON public.stewardship_configuration_version USING btree ((1)) WHERE (predecessor_id IS NULL);

-- INDEX: content_selected_slot
CREATE UNIQUE INDEX content_selected_slot ON public.stewardship_content_version USING btree (configuration_id, campaign_id, kind, slot) WHERE ((kind)::text = 'page'::text);

-- INDEX: credential_deployment_singleton
CREATE UNIQUE INDEX credential_deployment_singleton ON public.stewardship_credential_deployment USING btree ((1));

-- INDEX: daily_fact_lookup
CREATE INDEX daily_fact_lookup ON public.stewardship_daily_fact_set USING btree (campaign_id, population_scope, state);

-- INDEX: fact_demand_due
CREATE INDEX fact_demand_due ON public.stewardship_fact_demand USING btree (pending_due_at);

-- INDEX: family_session_expiry
CREATE INDEX family_session_expiry ON public.stewardship_family_session USING btree (expires_at, id);

-- INDEX: family_session_presence
CREATE INDEX family_session_presence ON public.stewardship_family_session USING btree (presence_at, id);

-- INDEX: occurrence_retry_command
CREATE UNIQUE INDEX occurrence_retry_command ON public.stewardship_occurrence_transition USING btree (occurrence_id, retry_command_id) WHERE (retry_command_id IS NOT NULL);

-- INDEX: operational_level_time
CREATE INDEX operational_level_time ON public.stewardship_operational_log USING btree (level, created_at);

-- INDEX: operator_recovery_operation
CREATE UNIQUE INDEX operator_recovery_operation ON public.stewardship_config_request USING btree (request_key) WHERE ((authority)::text = 'operator_recovery'::text);

-- INDEX: portal_session_expiry
CREATE INDEX portal_session_expiry ON public.stewardship_portal_session USING btree (expires_at);

-- INDEX: portal_session_principal
CREATE INDEX portal_session_principal ON public.stewardship_portal_session USING btree (principal_id, revoked_at);

-- INDEX: postclose_occurrence_once
CREATE UNIQUE INDEX postclose_occurrence_once ON public.stewardship_postclose_resolution USING btree (occurrence_id) WHERE (occurrence_id IS NOT NULL);

-- INDEX: postclose_outbox_once
CREATE UNIQUE INDEX postclose_outbox_once ON public.stewardship_postclose_resolution USING btree (outbox_id) WHERE (outbox_id IS NOT NULL);

-- INDEX: postclose_task_once
CREATE UNIQUE INDEX postclose_task_once ON public.stewardship_postclose_resolution USING btree (task_id) WHERE (task_id IS NOT NULL);

-- INDEX: schedule_occurrence_due
CREATE INDEX schedule_occurrence_due ON public.stewardship_schedule_occurrence USING btree (state, due_at);

-- INDEX: secret_expiry
CREATE INDEX secret_expiry ON public.stewardship_secret_request USING btree (state, expires_at);

-- INDEX: secret_one_pending_target
CREATE UNIQUE INDEX secret_one_pending_target ON public.stewardship_secret_request USING btree (target) WHERE ((state)::text = ANY ((ARRAY['staged'::character varying, 'testing'::character varying, 'installing'::character varying, 'awaiting_ack'::character varying, 'cleanup_pending'::character varying])::text[]));

-- INDEX: setup_mail_one_pending
CREATE UNIQUE INDEX setup_mail_one_pending ON public.stewardship_setup_mail_delivery USING btree (attempt_id) WHERE ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]));

-- INDEX: setup_one_completion
CREATE UNIQUE INDEX setup_one_completion ON public.stewardship_setup_completion USING btree ((1));

-- INDEX: setup_one_pending_attempt
CREATE UNIQUE INDEX setup_one_pending_attempt ON public.stewardship_setup_attempt USING btree ((1)) WHERE ((state)::text = ANY ((ARRAY['collecting'::character varying, 'loading'::character varying, 'frozen'::character varying])::text[]));

-- INDEX: setup_slack_one_pending
CREATE UNIQUE INDEX setup_slack_one_pending ON public.stewardship_setup_slack_delivery USING btree (attempt_id) WHERE ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]));

-- INDEX: source_refresh_pending_scope
CREATE INDEX source_refresh_pending_scope ON public.stewardship_source_refresh_request USING btree (window_digest, kind);

-- INDEX: stewardship_activation_catchup_campaign_id_ff26e1cc
CREATE INDEX stewardship_activation_catchup_campaign_id_ff26e1cc ON public.stewardship_activation_catchup USING btree (campaign_id);

-- INDEX: stewardship_activation_catchup_configuration_id_9837489b
CREATE INDEX stewardship_activation_catchup_configuration_id_9837489b ON public.stewardship_activation_catchup USING btree (configuration_id);

-- INDEX: stewardship_activation_catchup_correlation_id_3c8165a8
CREATE INDEX stewardship_activation_catchup_correlation_id_3c8165a8 ON public.stewardship_activation_catchup USING btree (correlation_id);

-- INDEX: stewardship_activation_catchup_task_root_id_35986c98
CREATE INDEX stewardship_activation_catchup_task_root_id_35986c98 ON public.stewardship_activation_catchup USING btree (task_root_id);

-- INDEX: stewardship_address_grant_correlation_id_9c829ca1
CREATE INDEX stewardship_address_grant_correlation_id_9c829ca1 ON public.stewardship_address_grant USING btree (correlation_id);

-- INDEX: stewardship_address_grant_rule_id_0dad4fcb
CREATE INDEX stewardship_address_grant_rule_id_0dad4fcb ON public.stewardship_address_grant USING btree (rule_id);

-- INDEX: stewardship_address_rule_configuration_id_c1b524bc
CREATE INDEX stewardship_address_rule_configuration_id_c1b524bc ON public.stewardship_address_rule USING btree (configuration_id);

-- INDEX: stewardship_address_rule_correlation_id_a5a9d184
CREATE INDEX stewardship_address_rule_correlation_id_a5a9d184 ON public.stewardship_address_rule USING btree (correlation_id);

-- INDEX: stewardship_admin_revocation_correlation_id_68b49474
CREATE INDEX stewardship_admin_revocation_correlation_id_68b49474 ON public.stewardship_admin_revocation USING btree (correlation_id);

-- INDEX: stewardship_applied_integration_configuration_id_9029654f
CREATE INDEX stewardship_applied_integration_configuration_id_9029654f ON public.stewardship_applied_integration USING btree (configuration_id);

-- INDEX: stewardship_applied_integration_correlation_id_ea0a565b
CREATE INDEX stewardship_applied_integration_correlation_id_ea0a565b ON public.stewardship_applied_integration USING btree (correlation_id);

-- INDEX: stewardship_assignment_overlay_correlation_id_287b6631
CREATE INDEX stewardship_assignment_overlay_correlation_id_287b6631 ON public.stewardship_assignment_overlay USING btree (correlation_id);

-- INDEX: stewardship_audit_context_correlation_id_e15f6c26
CREATE INDEX stewardship_audit_context_correlation_id_e15f6c26 ON public.stewardship_audit_context USING btree (correlation_id);

-- INDEX: stewardship_audit_event_campaign_reference_d4a9b6ea
CREATE INDEX stewardship_audit_event_campaign_reference_d4a9b6ea ON public.stewardship_audit_event USING btree (campaign_reference);

-- INDEX: stewardship_audit_event_correlation_id_88f7f688
CREATE INDEX stewardship_audit_event_correlation_id_88f7f688 ON public.stewardship_audit_event USING btree (correlation_id);

-- INDEX: stewardship_audit_event_parish_id_9fc8f11a
CREATE INDEX stewardship_audit_event_parish_id_9fc8f11a ON public.stewardship_audit_event USING btree (parish_id);

-- INDEX: stewardship_auth_incident_correlation_id_09dab55e
CREATE INDEX stewardship_auth_incident_correlation_id_09dab55e ON public.stewardship_auth_incident USING btree (correlation_id);

-- INDEX: stewardship_branding_asset_bundle_id_fb37a2e9
CREATE INDEX stewardship_branding_asset_bundle_id_fb37a2e9 ON public.stewardship_branding_asset USING btree (bundle_id);

-- INDEX: stewardship_branding_asset_correlation_id_d9049eee
CREATE INDEX stewardship_branding_asset_correlation_id_d9049eee ON public.stewardship_branding_asset USING btree (correlation_id);

-- INDEX: stewardship_branding_bundle_base_id_23ab9158
CREATE INDEX stewardship_branding_bundle_base_id_23ab9158 ON public.stewardship_branding_bundle USING btree (base_id);

-- INDEX: stewardship_branding_bundle_correlation_id_fc1c66eb
CREATE INDEX stewardship_branding_bundle_correlation_id_fc1c66eb ON public.stewardship_branding_bundle USING btree (correlation_id);

-- INDEX: stewardship_campaign_active_configuration_id_6a76077e
CREATE INDEX stewardship_campaign_active_configuration_id_6a76077e ON public.stewardship_campaign USING btree (active_configuration_id);

-- INDEX: stewardship_campaign_boundary_campaign_id_66b48563
CREATE INDEX stewardship_campaign_boundary_campaign_id_66b48563 ON public.stewardship_campaign_boundary USING btree (campaign_id);

-- INDEX: stewardship_campaign_boundary_correlation_id_ac266207
CREATE INDEX stewardship_campaign_boundary_correlation_id_ac266207 ON public.stewardship_campaign_boundary USING btree (correlation_id);

-- INDEX: stewardship_campaign_boundary_task_id_fc115736
CREATE INDEX stewardship_campaign_boundary_task_id_fc115736 ON public.stewardship_campaign_boundary USING btree (task_id);

-- INDEX: stewardship_campaign_config_abort_correlation_id_f74a6c9b
CREATE INDEX stewardship_campaign_config_abort_correlation_id_f74a6c9b ON public.stewardship_campaign_config_abort USING btree (correlation_id);

-- INDEX: stewardship_campaign_config_intent_campaign_id_ed1a1432
CREATE INDEX stewardship_campaign_config_intent_campaign_id_ed1a1432 ON public.stewardship_campaign_config_intent USING btree (campaign_id);

-- INDEX: stewardship_campaign_config_intent_correlation_id_bec08175
CREATE INDEX stewardship_campaign_config_intent_correlation_id_bec08175 ON public.stewardship_campaign_config_intent USING btree (correlation_id);

-- INDEX: stewardship_campaign_config_intent_prior_projection_id_1ef19299
CREATE INDEX stewardship_campaign_config_intent_prior_projection_id_1ef19299 ON public.stewardship_campaign_config_intent USING btree (prior_projection_id);

-- INDEX: stewardship_campaign_configuration_configuration_id_84fe667f
CREATE INDEX stewardship_campaign_configuration_configuration_id_84fe667f ON public.stewardship_campaign_configuration USING btree (configuration_id);

-- INDEX: stewardship_campaign_configuration_correlation_id_b0be00be
CREATE INDEX stewardship_campaign_configuration_correlation_id_b0be00be ON public.stewardship_campaign_configuration USING btree (correlation_id);

-- INDEX: stewardship_campaign_configuration_record_id_93919de9
CREATE INDEX stewardship_campaign_configuration_record_id_93919de9 ON public.stewardship_campaign_configuration USING btree (record_id);

-- INDEX: stewardship_campaign_control_campaign_id_ab98ed7a
CREATE INDEX stewardship_campaign_control_campaign_id_ab98ed7a ON public.stewardship_campaign_control USING btree (campaign_id);

-- INDEX: stewardship_campaign_control_correlation_id_00b6e629
CREATE INDEX stewardship_campaign_control_correlation_id_00b6e629 ON public.stewardship_campaign_control USING btree (correlation_id);

-- INDEX: stewardship_campaign_correlation_id_1b53c7d0
CREATE INDEX stewardship_campaign_correlation_id_1b53c7d0 ON public.stewardship_campaign USING btree (correlation_id);

-- INDEX: stewardship_campaign_credentials_correlation_id_2f8ee37d
CREATE INDEX stewardship_campaign_credentials_correlation_id_2f8ee37d ON public.stewardship_campaign_credentials USING btree (correlation_id);

-- INDEX: stewardship_campaign_credentials_rehearsal_epoch_id_9051a4f4
CREATE INDEX stewardship_campaign_credentials_rehearsal_epoch_id_9051a4f4 ON public.stewardship_campaign_credentials USING btree (rehearsal_epoch_id);

-- INDEX: stewardship_campaign_mail_test_campaign_id_eb2e98ab
CREATE INDEX stewardship_campaign_mail_test_campaign_id_eb2e98ab ON public.stewardship_campaign_mail_test USING btree (campaign_id);

-- INDEX: stewardship_campaign_mail_test_configuration_id_dc633885
CREATE INDEX stewardship_campaign_mail_test_configuration_id_dc633885 ON public.stewardship_campaign_mail_test USING btree (configuration_id);

-- INDEX: stewardship_campaign_mail_test_correlation_id_c29c728f
CREATE INDEX stewardship_campaign_mail_test_correlation_id_c29c728f ON public.stewardship_campaign_mail_test USING btree (correlation_id);

-- INDEX: stewardship_campaign_mail_test_run_id_84c77c43
CREATE INDEX stewardship_campaign_mail_test_run_id_84c77c43 ON public.stewardship_campaign_mail_test USING btree (run_id);

-- INDEX: stewardship_campaign_mail_test_template_id_2b3ea72e
CREATE INDEX stewardship_campaign_mail_test_template_id_2b3ea72e ON public.stewardship_campaign_mail_test USING btree (template_id);

-- INDEX: stewardship_campaign_transition_campaign_id_cfaa465b
CREATE INDEX stewardship_campaign_transition_campaign_id_cfaa465b ON public.stewardship_campaign_transition USING btree (campaign_id);

-- INDEX: stewardship_campaign_transition_configuration_id_e65133d6
CREATE INDEX stewardship_campaign_transition_configuration_id_e65133d6 ON public.stewardship_campaign_transition USING btree (configuration_id);

-- INDEX: stewardship_campaign_transition_correlation_id_63a422d2
CREATE INDEX stewardship_campaign_transition_correlation_id_63a422d2 ON public.stewardship_campaign_transition USING btree (correlation_id);

-- INDEX: stewardship_campaign_transition_prior_projection_id_444b9c8f
CREATE INDEX stewardship_campaign_transition_prior_projection_id_444b9c8f ON public.stewardship_campaign_transition USING btree (prior_projection_id);

-- INDEX: stewardship_campaign_work_gate_campaign_id_7ecf7387
CREATE INDEX stewardship_campaign_work_gate_campaign_id_7ecf7387 ON public.stewardship_campaign_work_gate USING btree (campaign_id);

-- INDEX: stewardship_campaign_work_gate_correlation_id_8ce3c1b1
CREATE INDEX stewardship_campaign_work_gate_correlation_id_8ce3c1b1 ON public.stewardship_campaign_work_gate USING btree (correlation_id);

-- INDEX: stewardship_catchup_checkpoint_correlation_id_96827021
CREATE INDEX stewardship_catchup_checkpoint_correlation_id_96827021 ON public.stewardship_catchup_checkpoint USING btree (correlation_id);

-- INDEX: stewardship_catchup_checkpoint_demand_id_4a41843c
CREATE INDEX stewardship_catchup_checkpoint_demand_id_4a41843c ON public.stewardship_catchup_checkpoint USING btree (demand_id);

-- INDEX: stewardship_catchup_checkpoint_task_id_a50a3998
CREATE INDEX stewardship_catchup_checkpoint_task_id_a50a3998 ON public.stewardship_catchup_checkpoint USING btree (task_id);

-- INDEX: stewardship_catchup_failure_correlation_id_56681e37
CREATE INDEX stewardship_catchup_failure_correlation_id_56681e37 ON public.stewardship_catchup_failure USING btree (correlation_id);

-- INDEX: stewardship_catchup_failure_demand_id_78213b0e
CREATE INDEX stewardship_catchup_failure_demand_id_78213b0e ON public.stewardship_catchup_failure USING btree (demand_id);

-- INDEX: stewardship_catchup_failure_task_id_6333f455
CREATE INDEX stewardship_catchup_failure_task_id_6333f455 ON public.stewardship_catchup_failure USING btree (task_id);

-- INDEX: stewardship_chair_reconciliation_configuration_id_31cd83cf
CREATE INDEX stewardship_chair_reconciliation_configuration_id_31cd83cf ON public.stewardship_chair_reconciliation USING btree (configuration_id);

-- INDEX: stewardship_chair_reconciliation_correlation_id_1acf90e7
CREATE INDEX stewardship_chair_reconciliation_correlation_id_1acf90e7 ON public.stewardship_chair_reconciliation USING btree (correlation_id);

-- INDEX: stewardship_chair_reconciliation_snapshot_id_e1a8872e
CREATE INDEX stewardship_chair_reconciliation_snapshot_id_e1a8872e ON public.stewardship_chair_reconciliation USING btree (snapshot_id);

-- INDEX: stewardship_chair_reconciliation_source_owner_id_c935bf61
CREATE INDEX stewardship_chair_reconciliation_source_owner_id_c935bf61 ON public.stewardship_chair_reconciliation USING btree (source_owner_id);

-- INDEX: stewardship_chair_review_assignment_record_id_306b9f09
CREATE INDEX stewardship_chair_review_assignment_record_id_306b9f09 ON public.stewardship_chair_review USING btree (assignment_record_id);

-- INDEX: stewardship_chair_review_closed_by_id_bd11ec76
CREATE INDEX stewardship_chair_review_closed_by_id_bd11ec76 ON public.stewardship_chair_review USING btree (closed_by_id);

-- INDEX: stewardship_chair_review_correlation_id_e7590072
CREATE INDEX stewardship_chair_review_correlation_id_e7590072 ON public.stewardship_chair_review USING btree (correlation_id);

-- INDEX: stewardship_chair_review_latest_by_id_6fcae486
CREATE INDEX stewardship_chair_review_latest_by_id_6fcae486 ON public.stewardship_chair_review USING btree (latest_by_id);

-- INDEX: stewardship_chair_review_opened_by_id_8594b2ca
CREATE INDEX stewardship_chair_review_opened_by_id_8594b2ca ON public.stewardship_chair_review USING btree (opened_by_id);

-- INDEX: stewardship_chair_seed_evidence_correlation_id_e2bf03ef
CREATE INDEX stewardship_chair_seed_evidence_correlation_id_e2bf03ef ON public.stewardship_chair_seed_evidence USING btree (correlation_id);

-- INDEX: stewardship_chair_seed_evidence_snapshot_id_f6051619
CREATE INDEX stewardship_chair_seed_evidence_snapshot_id_f6051619 ON public.stewardship_chair_seed_evidence USING btree (snapshot_id);

-- INDEX: stewardship_config_activation_correlation_id_f2a05f43
CREATE INDEX stewardship_config_activation_correlation_id_f2a05f43 ON public.stewardship_config_activation USING btree (correlation_id);

-- INDEX: stewardship_config_activation_predecessor_id_797080e8
CREATE INDEX stewardship_config_activation_predecessor_id_797080e8 ON public.stewardship_config_activation USING btree (predecessor_id);

-- INDEX: stewardship_config_checkpoint_correlation_id_f7d67dbf
CREATE INDEX stewardship_config_checkpoint_correlation_id_f7d67dbf ON public.stewardship_config_checkpoint USING btree (correlation_id);

-- INDEX: stewardship_config_checkpoint_request_id_43a03b17
CREATE INDEX stewardship_config_checkpoint_request_id_43a03b17 ON public.stewardship_config_checkpoint USING btree (request_id);

-- INDEX: stewardship_config_request_base_id_fe781cd2
CREATE INDEX stewardship_config_request_base_id_fe781cd2 ON public.stewardship_config_request USING btree (base_id);

-- INDEX: stewardship_config_request_candidate_digest_9f74164f_like
CREATE INDEX stewardship_config_request_candidate_digest_9f74164f_like ON public.stewardship_config_request USING btree (candidate_digest varchar_pattern_ops);

-- INDEX: stewardship_config_request_correlation_id_93c8da42
CREATE INDEX stewardship_config_request_correlation_id_93c8da42 ON public.stewardship_config_request USING btree (correlation_id);

-- INDEX: stewardship_configuration_version_correlation_id_2787c5c5
CREATE INDEX stewardship_configuration_version_correlation_id_2787c5c5 ON public.stewardship_configuration_version USING btree (correlation_id);

-- INDEX: stewardship_configuration_version_digest_78a6efb1_like
CREATE INDEX stewardship_configuration_version_digest_78a6efb1_like ON public.stewardship_configuration_version USING btree (digest varchar_pattern_ops);

-- INDEX: stewardship_configuration_version_predecessor_id_ded3a24f
CREATE INDEX stewardship_configuration_version_predecessor_id_ded3a24f ON public.stewardship_configuration_version USING btree (predecessor_id);

-- INDEX: stewardship_content_version_campaign_id_e6ef043b
CREATE INDEX stewardship_content_version_campaign_id_e6ef043b ON public.stewardship_content_version USING btree (campaign_id);

-- INDEX: stewardship_content_version_configuration_id_7bb46bdb
CREATE INDEX stewardship_content_version_configuration_id_7bb46bdb ON public.stewardship_content_version USING btree (configuration_id);

-- INDEX: stewardship_content_version_correlation_id_259fc883
CREATE INDEX stewardship_content_version_correlation_id_259fc883 ON public.stewardship_content_version USING btree (correlation_id);

-- INDEX: stewardship_content_version_record_id_15c5e626
CREATE INDEX stewardship_content_version_record_id_15c5e626 ON public.stewardship_content_version USING btree (record_id);

-- INDEX: stewardship_credential_consumer_ack_correlation_id_3c0b06b8
CREATE INDEX stewardship_credential_consumer_ack_correlation_id_3c0b06b8 ON public.stewardship_credential_consumer_ack USING btree (correlation_id);

-- INDEX: stewardship_credential_consumer_ack_request_id_815d7b63
CREATE INDEX stewardship_credential_consumer_ack_request_id_815d7b63 ON public.stewardship_credential_consumer_ack USING btree (request_id);

-- INDEX: stewardship_credential_deployment_correlation_id_4c90b4bf
CREATE INDEX stewardship_credential_deployment_correlation_id_4c90b4bf ON public.stewardship_credential_deployment USING btree (correlation_id);

-- INDEX: stewardship_credential_key_state_correlation_id_b14cc912
CREATE INDEX stewardship_credential_key_state_correlation_id_b14cc912 ON public.stewardship_credential_key_state USING btree (correlation_id);

-- INDEX: stewardship_credential_key_state_kind_ba19bfb3_like
CREATE INDEX stewardship_credential_key_state_kind_ba19bfb3_like ON public.stewardship_credential_key_state USING btree (kind varchar_pattern_ops);

-- INDEX: stewardship_daily_fact_correlation_id_280918ca
CREATE INDEX stewardship_daily_fact_correlation_id_280918ca ON public.stewardship_daily_fact USING btree (correlation_id);

-- INDEX: stewardship_daily_fact_fact_set_id_16f81142
CREATE INDEX stewardship_daily_fact_fact_set_id_16f81142 ON public.stewardship_daily_fact USING btree (fact_set_id);

-- INDEX: stewardship_daily_fact_set_campaign_id_de8bb40b
CREATE INDEX stewardship_daily_fact_set_campaign_id_de8bb40b ON public.stewardship_daily_fact_set USING btree (campaign_id);

-- INDEX: stewardship_daily_fact_set_correlation_id_90c3f833
CREATE INDEX stewardship_daily_fact_set_correlation_id_90c3f833 ON public.stewardship_daily_fact_set USING btree (correlation_id);

-- INDEX: stewardship_daily_fact_set_source_id_e4b5981e
CREATE INDEX stewardship_daily_fact_set_source_id_e4b5981e ON public.stewardship_daily_fact_set USING btree (source_id);

-- INDEX: stewardship_daily_fact_set_task_id_f748238f
CREATE INDEX stewardship_daily_fact_set_task_id_f748238f ON public.stewardship_daily_fact_set USING btree (task_id);

-- INDEX: stewardship_daily_fact_set_timezone_configuration_id_b7346e6e
CREATE INDEX stewardship_daily_fact_set_timezone_configuration_id_b7346e6e ON public.stewardship_daily_fact_set USING btree (timezone_configuration_id);

-- INDEX: stewardship_domain_rule_configuration_id_4231e000
CREATE INDEX stewardship_domain_rule_configuration_id_4231e000 ON public.stewardship_domain_rule USING btree (configuration_id);

-- INDEX: stewardship_domain_rule_correlation_id_0f547afa
CREATE INDEX stewardship_domain_rule_correlation_id_0f547afa ON public.stewardship_domain_rule USING btree (correlation_id);

-- INDEX: stewardship_fact_compaction_correlation_id_75b04fdd
CREATE INDEX stewardship_fact_compaction_correlation_id_75b04fdd ON public.stewardship_fact_compaction USING btree (correlation_id);

-- INDEX: stewardship_fact_compaction_task_id_aea57528
CREATE INDEX stewardship_fact_compaction_task_id_aea57528 ON public.stewardship_fact_compaction USING btree (task_id);

-- INDEX: stewardship_fact_demand_campaign_id_a0b2c939
CREATE INDEX stewardship_fact_demand_campaign_id_a0b2c939 ON public.stewardship_fact_demand USING btree (campaign_id);

-- INDEX: stewardship_fact_demand_claimed_generation_id_9c50a239
CREATE INDEX stewardship_fact_demand_claimed_generation_id_9c50a239 ON public.stewardship_fact_demand USING btree (claimed_generation_id);

-- INDEX: stewardship_fact_demand_claimed_task_id_6613aeca
CREATE INDEX stewardship_fact_demand_claimed_task_id_6613aeca ON public.stewardship_fact_demand USING btree (claimed_task_id);

-- INDEX: stewardship_fact_demand_correlation_id_3157eec2
CREATE INDEX stewardship_fact_demand_correlation_id_3157eec2 ON public.stewardship_fact_demand USING btree (correlation_id);

-- INDEX: stewardship_fact_demand_requested_source_id_5589a5cb
CREATE INDEX stewardship_fact_demand_requested_source_id_5589a5cb ON public.stewardship_fact_demand USING btree (requested_source_id);

-- INDEX: stewardship_fact_demand_requested_timezone_configu_fc08ec37
CREATE INDEX stewardship_fact_demand_requested_timezone_configu_fc08ec37 ON public.stewardship_fact_demand USING btree (requested_timezone_configuration_id);

-- INDEX: stewardship_fact_pin_correlation_id_b605320b
CREATE INDEX stewardship_fact_pin_correlation_id_b605320b ON public.stewardship_fact_pin USING btree (correlation_id);

-- INDEX: stewardship_fact_pin_fact_set_id_2ff4a4d8
CREATE INDEX stewardship_fact_pin_fact_set_id_2ff4a4d8 ON public.stewardship_fact_pin USING btree (fact_set_id);

-- INDEX: stewardship_fact_pointer_campaign_id_5ecd1fb9
CREATE INDEX stewardship_fact_pointer_campaign_id_5ecd1fb9 ON public.stewardship_fact_pointer USING btree (campaign_id);

-- INDEX: stewardship_fact_pointer_correlation_id_c9e67ee8
CREATE INDEX stewardship_fact_pointer_correlation_id_c9e67ee8 ON public.stewardship_fact_pointer USING btree (correlation_id);

-- INDEX: stewardship_fact_pointer_fact_set_id_f3e5219f
CREATE INDEX stewardship_fact_pointer_fact_set_id_f3e5219f ON public.stewardship_fact_pointer USING btree (fact_set_id);

-- INDEX: stewardship_family_campaign_campaign_id_a836c089
CREATE INDEX stewardship_family_campaign_campaign_id_a836c089 ON public.stewardship_family_campaign USING btree (campaign_id);

-- INDEX: stewardship_family_campaign_correlation_id_4c195d37
CREATE INDEX stewardship_family_campaign_correlation_id_4c195d37 ON public.stewardship_family_campaign USING btree (correlation_id);

-- INDEX: stewardship_family_code_mac_campaign_id_cb036b47
CREATE INDEX stewardship_family_code_mac_campaign_id_cb036b47 ON public.stewardship_family_code_mac USING btree (campaign_id);

-- INDEX: stewardship_family_code_mac_correlation_id_9ac27883
CREATE INDEX stewardship_family_code_mac_correlation_id_9ac27883 ON public.stewardship_family_code_mac USING btree (correlation_id);

-- INDEX: stewardship_family_code_mac_family_id_bfdb0267
CREATE INDEX stewardship_family_code_mac_family_id_bfdb0267 ON public.stewardship_family_code_mac USING btree (family_id);

-- INDEX: stewardship_family_eligibility_correlation_id_3f01136b
CREATE INDEX stewardship_family_eligibility_correlation_id_3f01136b ON public.stewardship_family_eligibility USING btree (correlation_id);

-- INDEX: stewardship_family_eligibility_family_id_97f5cc47
CREATE INDEX stewardship_family_eligibility_family_id_97f5cc47 ON public.stewardship_family_eligibility USING btree (family_id);

-- INDEX: stewardship_family_session_correlation_id_3c971bd6
CREATE INDEX stewardship_family_session_correlation_id_3c971bd6 ON public.stewardship_family_session USING btree (correlation_id);

-- INDEX: stewardship_family_session_family_id_330cde00
CREATE INDEX stewardship_family_session_family_id_330cde00 ON public.stewardship_family_session USING btree (family_id);

-- INDEX: stewardship_family_session_rehearsal_epoch_id_40b4c432
CREATE INDEX stewardship_family_session_rehearsal_epoch_id_40b4c432 ON public.stewardship_family_session USING btree (rehearsal_epoch_id);

-- INDEX: stewardship_family_session_session_id_5ff9c26d_like
CREATE INDEX stewardship_family_session_session_id_5ff9c26d_like ON public.stewardship_family_session USING btree (session_id varchar_pattern_ops);

-- INDEX: stewardship_family_token_campaign_id_347844ce
CREATE INDEX stewardship_family_token_campaign_id_347844ce ON public.stewardship_family_token USING btree (campaign_id);

-- INDEX: stewardship_family_token_correlation_id_18fd31fe
CREATE INDEX stewardship_family_token_correlation_id_18fd31fe ON public.stewardship_family_token USING btree (correlation_id);

-- INDEX: stewardship_family_token_family_id_5b602375
CREATE INDEX stewardship_family_token_family_id_5b602375 ON public.stewardship_family_token USING btree (family_id);

-- INDEX: stewardship_family_token_g_configuration_request_id_9874afb9
CREATE INDEX stewardship_family_token_g_configuration_request_id_9874afb9 ON public.stewardship_family_token_generation USING btree (configuration_request_id);

-- INDEX: stewardship_family_token_generation_campaign_id_4a0bf6dd
CREATE INDEX stewardship_family_token_generation_campaign_id_4a0bf6dd ON public.stewardship_family_token_generation USING btree (campaign_id);

-- INDEX: stewardship_family_token_generation_configuration_id_4258ab65
CREATE INDEX stewardship_family_token_generation_configuration_id_4258ab65 ON public.stewardship_family_token_generation USING btree (configuration_id);

-- INDEX: stewardship_family_token_generation_correlation_id_63ba5758
CREATE INDEX stewardship_family_token_generation_correlation_id_63ba5758 ON public.stewardship_family_token_generation USING btree (correlation_id);

-- INDEX: stewardship_family_token_generation_id_6bfa7076
CREATE INDEX stewardship_family_token_generation_id_6bfa7076 ON public.stewardship_family_token USING btree (generation_id);

-- INDEX: stewardship_limiter_health_correlation_id_e682d7b2
CREATE INDEX stewardship_limiter_health_correlation_id_e682d7b2 ON public.stewardship_limiter_health USING btree (correlation_id);

-- INDEX: stewardship_limiter_health_namespace_fingerprint_dbe891db_like
CREATE INDEX stewardship_limiter_health_namespace_fingerprint_dbe891db_like ON public.stewardship_limiter_health USING btree (namespace_fingerprint varchar_pattern_ops);

-- INDEX: stewardship_ministry_activity_configuration_id_68b9cd45
CREATE INDEX stewardship_ministry_activity_configuration_id_68b9cd45 ON public.stewardship_ministry_activity USING btree (configuration_id);

-- INDEX: stewardship_ministry_activity_correlation_id_bcdaa576
CREATE INDEX stewardship_ministry_activity_correlation_id_bcdaa576 ON public.stewardship_ministry_activity USING btree (correlation_id);

-- INDEX: stewardship_ministry_assignment_configuration_id_5f298c17
CREATE INDEX stewardship_ministry_assignment_configuration_id_5f298c17 ON public.stewardship_ministry_assignment USING btree (configuration_id);

-- INDEX: stewardship_ministry_assignment_correlation_id_8361162c
CREATE INDEX stewardship_ministry_assignment_correlation_id_8361162c ON public.stewardship_ministry_assignment USING btree (correlation_id);

-- INDEX: stewardship_oauth_consumption_expires_at_132b3260
CREATE INDEX stewardship_oauth_consumption_expires_at_132b3260 ON public.stewardship_oauth_consumption USING btree (expires_at);

-- INDEX: stewardship_oauth_consumption_fingerprint_991069b6_like
CREATE INDEX stewardship_oauth_consumption_fingerprint_991069b6_like ON public.stewardship_oauth_consumption USING btree (fingerprint varchar_pattern_ops);

-- INDEX: stewardship_occurrence_transition_correlation_id_7bd83ab2
CREATE INDEX stewardship_occurrence_transition_correlation_id_7bd83ab2 ON public.stewardship_occurrence_transition USING btree (correlation_id);

-- INDEX: stewardship_occurrence_transition_occurrence_id_f136d622
CREATE INDEX stewardship_occurrence_transition_occurrence_id_f136d622 ON public.stewardship_occurrence_transition USING btree (occurrence_id);

-- INDEX: stewardship_operational_log_correlation_id_b9358035
CREATE INDEX stewardship_operational_log_correlation_id_b9358035 ON public.stewardship_operational_log USING btree (correlation_id);

-- INDEX: stewardship_parish_correlation_id_35346fd4
CREATE INDEX stewardship_parish_correlation_id_35346fd4 ON public.stewardship_parish USING btree (correlation_id);

-- INDEX: stewardship_parish_record_id_b1d7afb4
CREATE INDEX stewardship_parish_record_id_b1d7afb4 ON public.stewardship_parish USING btree (record_id);

-- INDEX: stewardship_policy_epoch_correlation_id_f37b28aa
CREATE INDEX stewardship_policy_epoch_correlation_id_f37b28aa ON public.stewardship_policy_epoch USING btree (correlation_id);

-- INDEX: stewardship_policy_security_event_activation_id_a6ee0ad0
CREATE INDEX stewardship_policy_security_event_activation_id_a6ee0ad0 ON public.stewardship_policy_security_event USING btree (activation_id);

-- INDEX: stewardship_policy_security_event_correlation_id_9ddfae2a
CREATE INDEX stewardship_policy_security_event_correlation_id_9ddfae2a ON public.stewardship_policy_security_event USING btree (correlation_id);

-- INDEX: stewardship_portal_session_correlation_id_9a110953
CREATE INDEX stewardship_portal_session_correlation_id_9a110953 ON public.stewardship_portal_session USING btree (correlation_id);

-- INDEX: stewardship_portal_session_session_id_0eb11329_like
CREATE INDEX stewardship_portal_session_session_id_0eb11329_like ON public.stewardship_portal_session USING btree (session_id varchar_pattern_ops);

-- INDEX: stewardship_portal_user_correlation_id_668e2dfb
CREATE INDEX stewardship_portal_user_correlation_id_668e2dfb ON public.stewardship_portal_user USING btree (correlation_id);

-- INDEX: stewardship_portal_user_email_7dcaba66
CREATE INDEX stewardship_portal_user_email_7dcaba66 ON public.stewardship_portal_user USING btree (email);

-- INDEX: stewardship_portal_user_email_7dcaba66_like
CREATE INDEX stewardship_portal_user_email_7dcaba66_like ON public.stewardship_portal_user USING btree (email varchar_pattern_ops);

-- INDEX: stewardship_portal_user_google_subject_16e5b015_like
CREATE INDEX stewardship_portal_user_google_subject_16e5b015_like ON public.stewardship_portal_user USING btree (google_subject varchar_pattern_ops);

-- INDEX: stewardship_postclose_resolution_campaign_id_8bfa8023
CREATE INDEX stewardship_postclose_resolution_campaign_id_8bfa8023 ON public.stewardship_postclose_resolution USING btree (campaign_id);

-- INDEX: stewardship_postclose_resolution_correlation_id_4e75e79f
CREATE INDEX stewardship_postclose_resolution_correlation_id_4e75e79f ON public.stewardship_postclose_resolution USING btree (correlation_id);

-- INDEX: stewardship_postclose_resolution_occurrence_id_850a55fd
CREATE INDEX stewardship_postclose_resolution_occurrence_id_850a55fd ON public.stewardship_postclose_resolution USING btree (occurrence_id);

-- INDEX: stewardship_postclose_resolution_task_id_f4814b75
CREATE INDEX stewardship_postclose_resolution_task_id_f4814b75 ON public.stewardship_postclose_resolution USING btree (task_id);

-- INDEX: stewardship_provider_context_correlation_id_c8e75ee4
CREATE INDEX stewardship_provider_context_correlation_id_c8e75ee4 ON public.stewardship_provider_context USING btree (correlation_id);

-- INDEX: stewardship_public_credential_handoff_correlation_id_ed52497e
CREATE INDEX stewardship_public_credential_handoff_correlation_id_ed52497e ON public.stewardship_public_credential_handoff USING btree (correlation_id);

-- INDEX: stewardship_public_credential_handoff_target_9c230871_like
CREATE INDEX stewardship_public_credential_handoff_target_9c230871_like ON public.stewardship_public_credential_handoff USING btree (target varchar_pattern_ops);

-- INDEX: stewardship_rehearsal_code_mac_correlation_id_f9eee84e
CREATE INDEX stewardship_rehearsal_code_mac_correlation_id_f9eee84e ON public.stewardship_rehearsal_code_mac USING btree (correlation_id);

-- INDEX: stewardship_rehearsal_code_mac_credential_id_e7b83882
CREATE INDEX stewardship_rehearsal_code_mac_credential_id_e7b83882 ON public.stewardship_rehearsal_code_mac USING btree (credential_id);

-- INDEX: stewardship_rehearsal_code_mac_epoch_id_883778c1
CREATE INDEX stewardship_rehearsal_code_mac_epoch_id_883778c1 ON public.stewardship_rehearsal_code_mac USING btree (epoch_id);

-- INDEX: stewardship_rehearsal_credential_correlation_id_7978dbdd
CREATE INDEX stewardship_rehearsal_credential_correlation_id_7978dbdd ON public.stewardship_rehearsal_credential USING btree (correlation_id);

-- INDEX: stewardship_rehearsal_credential_epoch_id_abc6bce4
CREATE INDEX stewardship_rehearsal_credential_epoch_id_abc6bce4 ON public.stewardship_rehearsal_credential USING btree (epoch_id);

-- INDEX: stewardship_rehearsal_credential_family_id_78761ef6
CREATE INDEX stewardship_rehearsal_credential_family_id_78761ef6 ON public.stewardship_rehearsal_credential USING btree (family_id);

-- INDEX: stewardship_rehearsal_epoch_campaign_id_4b5e57ff
CREATE INDEX stewardship_rehearsal_epoch_campaign_id_4b5e57ff ON public.stewardship_rehearsal_epoch USING btree (campaign_id);

-- INDEX: stewardship_rehearsal_epoch_correlation_id_794768e0
CREATE INDEX stewardship_rehearsal_epoch_correlation_id_794768e0 ON public.stewardship_rehearsal_epoch USING btree (correlation_id);

-- INDEX: stewardship_rehearsal_reservation_campaign_id_e7ce330f
CREATE INDEX stewardship_rehearsal_reservation_campaign_id_e7ce330f ON public.stewardship_rehearsal_reservation USING btree (campaign_id);

-- INDEX: stewardship_restore_delive_recovery_occurrence_id_db5d60b4
CREATE INDEX stewardship_restore_delive_recovery_occurrence_id_db5d60b4 ON public.stewardship_restore_delivery_hold USING btree (recovery_occurrence_id);

-- INDEX: stewardship_restore_delivery_hold_correlation_id_d5479a65
CREATE INDEX stewardship_restore_delivery_hold_correlation_id_d5479a65 ON public.stewardship_restore_delivery_hold USING btree (correlation_id);

-- INDEX: stewardship_restore_delivery_hold_definition_id_b44fcfb3
CREATE INDEX stewardship_restore_delivery_hold_definition_id_b44fcfb3 ON public.stewardship_restore_delivery_hold USING btree (definition_id);

-- INDEX: stewardship_restore_hold_r_recovery_occurrence_id_529b01e5
CREATE INDEX stewardship_restore_hold_r_recovery_occurrence_id_529b01e5 ON public.stewardship_restore_hold_resolution USING btree (recovery_occurrence_id);

-- INDEX: stewardship_restore_hold_resolution_correlation_id_4851687b
CREATE INDEX stewardship_restore_hold_resolution_correlation_id_4851687b ON public.stewardship_restore_hold_resolution USING btree (correlation_id);

-- INDEX: stewardship_restore_hold_resolution_hold_id_ac13f013
CREATE INDEX stewardship_restore_hold_resolution_hold_id_ac13f013 ON public.stewardship_restore_hold_resolution USING btree (hold_id);

-- INDEX: stewardship_runtime_transition_correlation_id_c7781b4a
CREATE INDEX stewardship_runtime_transition_correlation_id_c7781b4a ON public.stewardship_runtime_transition USING btree (correlation_id);

-- INDEX: stewardship_schedule_definition_campaign_id_3555aef5
CREATE INDEX stewardship_schedule_definition_campaign_id_3555aef5 ON public.stewardship_schedule_definition USING btree (campaign_id);

-- INDEX: stewardship_schedule_definition_correlation_id_efe7b1aa
CREATE INDEX stewardship_schedule_definition_correlation_id_efe7b1aa ON public.stewardship_schedule_definition USING btree (correlation_id);

-- INDEX: stewardship_schedule_definition_current_revision_id_fc0ba17f
CREATE INDEX stewardship_schedule_definition_current_revision_id_fc0ba17f ON public.stewardship_schedule_definition USING btree (current_revision_id);

-- INDEX: stewardship_schedule_fulfillment_correlation_id_ab69b388
CREATE INDEX stewardship_schedule_fulfillment_correlation_id_ab69b388 ON public.stewardship_schedule_fulfillment USING btree (correlation_id);

-- INDEX: stewardship_schedule_fulfillment_definition_id_6fdd18fb
CREATE INDEX stewardship_schedule_fulfillment_definition_id_6fdd18fb ON public.stewardship_schedule_fulfillment USING btree (definition_id);

-- INDEX: stewardship_schedule_fulfillment_occurrence_id_049d97a8
CREATE INDEX stewardship_schedule_fulfillment_occurrence_id_049d97a8 ON public.stewardship_schedule_fulfillment USING btree (occurrence_id);

-- INDEX: stewardship_schedule_occurrence_correlation_id_cead872a
CREATE INDEX stewardship_schedule_occurrence_correlation_id_cead872a ON public.stewardship_schedule_occurrence USING btree (correlation_id);

-- INDEX: stewardship_schedule_occurrence_definition_id_af427c05
CREATE INDEX stewardship_schedule_occurrence_definition_id_af427c05 ON public.stewardship_schedule_occurrence USING btree (definition_id);

-- INDEX: stewardship_schedule_occurrence_occurrence_key_5d1858c2_like
CREATE INDEX stewardship_schedule_occurrence_occurrence_key_5d1858c2_like ON public.stewardship_schedule_occurrence USING btree (occurrence_key varchar_pattern_ops);

-- INDEX: stewardship_schedule_occurrence_replacement_id_de647db6
CREATE INDEX stewardship_schedule_occurrence_replacement_id_de647db6 ON public.stewardship_schedule_occurrence USING btree (replacement_id);

-- INDEX: stewardship_schedule_occurrence_revision_id_e39867be
CREATE INDEX stewardship_schedule_occurrence_revision_id_e39867be ON public.stewardship_schedule_occurrence USING btree (revision_id);

-- INDEX: stewardship_schedule_occurrence_task_id_87b5e54c
CREATE INDEX stewardship_schedule_occurrence_task_id_87b5e54c ON public.stewardship_schedule_occurrence USING btree (task_id);

-- INDEX: stewardship_schedule_revision_campaign_id_8049175a
CREATE INDEX stewardship_schedule_revision_campaign_id_8049175a ON public.stewardship_schedule_revision USING btree (campaign_id);

-- INDEX: stewardship_schedule_revision_configuration_id_fbf8947f
CREATE INDEX stewardship_schedule_revision_configuration_id_fbf8947f ON public.stewardship_schedule_revision USING btree (configuration_id);

-- INDEX: stewardship_schedule_revision_correlation_id_34d773f3
CREATE INDEX stewardship_schedule_revision_correlation_id_34d773f3 ON public.stewardship_schedule_revision USING btree (correlation_id);

-- INDEX: stewardship_schedule_revision_record_id_001066ef
CREATE INDEX stewardship_schedule_revision_record_id_001066ef ON public.stewardship_schedule_revision USING btree (record_id);

-- INDEX: stewardship_schedule_selection_configuration_id_c690f572
CREATE INDEX stewardship_schedule_selection_configuration_id_c690f572 ON public.stewardship_schedule_selection USING btree (configuration_id);

-- INDEX: stewardship_schedule_selection_correlation_id_ba99fea8
CREATE INDEX stewardship_schedule_selection_correlation_id_ba99fea8 ON public.stewardship_schedule_selection USING btree (correlation_id);

-- INDEX: stewardship_schedule_selection_definition_id_24fa15bc
CREATE INDEX stewardship_schedule_selection_definition_id_24fa15bc ON public.stewardship_schedule_selection USING btree (definition_id);

-- INDEX: stewardship_schedule_selection_previous_revision_id_fee2cd72
CREATE INDEX stewardship_schedule_selection_previous_revision_id_fee2cd72 ON public.stewardship_schedule_selection USING btree (previous_revision_id);

-- INDEX: stewardship_schedule_selection_selected_revision_id_7f42ce52
CREATE INDEX stewardship_schedule_selection_selected_revision_id_7f42ce52 ON public.stewardship_schedule_selection USING btree (selected_revision_id);

-- INDEX: stewardship_secret_checkpoint_correlation_id_0dca8ead
CREATE INDEX stewardship_secret_checkpoint_correlation_id_0dca8ead ON public.stewardship_secret_checkpoint USING btree (correlation_id);

-- INDEX: stewardship_secret_checkpoint_request_id_b256dff2
CREATE INDEX stewardship_secret_checkpoint_request_id_b256dff2 ON public.stewardship_secret_checkpoint USING btree (request_id);

-- INDEX: stewardship_secret_request_correlation_id_cea0fb71
CREATE INDEX stewardship_secret_request_correlation_id_cea0fb71 ON public.stewardship_secret_request USING btree (correlation_id);

-- INDEX: stewardship_setup_attempt_base_id_91cd06e4
CREATE INDEX stewardship_setup_attempt_base_id_91cd06e4 ON public.stewardship_setup_attempt USING btree (base_id);

-- INDEX: stewardship_setup_attempt_correlation_id_c9ca609d
CREATE INDEX stewardship_setup_attempt_correlation_id_c9ca609d ON public.stewardship_setup_attempt USING btree (correlation_id);

-- INDEX: stewardship_setup_completion_correlation_id_058b2480
CREATE INDEX stewardship_setup_completion_correlation_id_058b2480 ON public.stewardship_setup_completion USING btree (correlation_id);

-- INDEX: stewardship_setup_config_abort_correlation_id_7f1e34c4
CREATE INDEX stewardship_setup_config_abort_correlation_id_7f1e34c4 ON public.stewardship_setup_config_abort USING btree (correlation_id);

-- INDEX: stewardship_setup_config_intent_correlation_id_ff8e454c
CREATE INDEX stewardship_setup_config_intent_correlation_id_ff8e454c ON public.stewardship_setup_config_intent USING btree (correlation_id);

-- INDEX: stewardship_setup_credential_install_correlation_id_656d561f
CREATE INDEX stewardship_setup_credential_install_correlation_id_656d561f ON public.stewardship_setup_credential_install USING btree (correlation_id);

-- INDEX: stewardship_setup_credential_install_readiness_id_7969f012
CREATE INDEX stewardship_setup_credential_install_readiness_id_7969f012 ON public.stewardship_setup_credential_install USING btree (readiness_id);

-- INDEX: stewardship_setup_draft_section_attempt_id_58448a74
CREATE INDEX stewardship_setup_draft_section_attempt_id_58448a74 ON public.stewardship_setup_draft_section USING btree (attempt_id);

-- INDEX: stewardship_setup_draft_section_correlation_id_a207d757
CREATE INDEX stewardship_setup_draft_section_correlation_id_a207d757 ON public.stewardship_setup_draft_section USING btree (correlation_id);

-- INDEX: stewardship_setup_mail_delivery_attempt_id_befe550c
CREATE INDEX stewardship_setup_mail_delivery_attempt_id_befe550c ON public.stewardship_setup_mail_delivery USING btree (attempt_id);

-- INDEX: stewardship_setup_mail_delivery_correlation_id_fa83c8cf
CREATE INDEX stewardship_setup_mail_delivery_correlation_id_fa83c8cf ON public.stewardship_setup_mail_delivery USING btree (correlation_id);

-- INDEX: stewardship_setup_mail_delivery_credential_id_24219ecf
CREATE INDEX stewardship_setup_mail_delivery_credential_id_24219ecf ON public.stewardship_setup_mail_delivery USING btree (credential_id);

-- INDEX: stewardship_setup_mail_delivery_run_id_e8502211
CREATE INDEX stewardship_setup_mail_delivery_run_id_e8502211 ON public.stewardship_setup_mail_delivery USING btree (run_id);

-- INDEX: stewardship_setup_mail_exchange_correlation_id_9e0492e6
CREATE INDEX stewardship_setup_mail_exchange_correlation_id_9e0492e6 ON public.stewardship_setup_mail_exchange USING btree (correlation_id);

-- INDEX: stewardship_setup_mail_exchange_delivery_id_82256d92
CREATE INDEX stewardship_setup_mail_exchange_delivery_id_82256d92 ON public.stewardship_setup_mail_exchange USING btree (delivery_id);

-- INDEX: stewardship_setup_mail_exchange_run_id_98d28b1b
CREATE INDEX stewardship_setup_mail_exchange_run_id_98d28b1b ON public.stewardship_setup_mail_exchange USING btree (run_id);

-- INDEX: stewardship_setup_prepared_correlation_id_bd1d76ca
CREATE INDEX stewardship_setup_prepared_correlation_id_bd1d76ca ON public.stewardship_setup_prepared USING btree (correlation_id);

-- INDEX: stewardship_setup_readiness_binding_correlation_id_05297160
CREATE INDEX stewardship_setup_readiness_binding_correlation_id_05297160 ON public.stewardship_setup_readiness_binding USING btree (correlation_id);

-- INDEX: stewardship_setup_readiness_binding_mail_delivery_id_d122b14b
CREATE INDEX stewardship_setup_readiness_binding_mail_delivery_id_d122b14b ON public.stewardship_setup_readiness_binding USING btree (mail_delivery_id);

-- INDEX: stewardship_setup_readiness_binding_slack_delivery_id_29b81023
CREATE INDEX stewardship_setup_readiness_binding_slack_delivery_id_29b81023 ON public.stewardship_setup_readiness_binding USING btree (slack_delivery_id);

-- INDEX: stewardship_setup_readiness_binding_source_result_id_e33ae430
CREATE INDEX stewardship_setup_readiness_binding_source_result_id_e33ae430 ON public.stewardship_setup_readiness_binding USING btree (source_result_id);

-- INDEX: stewardship_setup_sealed_credential_attempt_id_6b5927d4
CREATE INDEX stewardship_setup_sealed_credential_attempt_id_6b5927d4 ON public.stewardship_setup_sealed_credential USING btree (attempt_id);

-- INDEX: stewardship_setup_sealed_credential_correlation_id_1034a584
CREATE INDEX stewardship_setup_sealed_credential_correlation_id_1034a584 ON public.stewardship_setup_sealed_credential USING btree (correlation_id);

-- INDEX: stewardship_setup_slack_delivery_attempt_id_100dafb1
CREATE INDEX stewardship_setup_slack_delivery_attempt_id_100dafb1 ON public.stewardship_setup_slack_delivery USING btree (attempt_id);

-- INDEX: stewardship_setup_slack_delivery_correlation_id_f57e82e8
CREATE INDEX stewardship_setup_slack_delivery_correlation_id_f57e82e8 ON public.stewardship_setup_slack_delivery USING btree (correlation_id);

-- INDEX: stewardship_setup_slack_delivery_credential_id_8b1f948b
CREATE INDEX stewardship_setup_slack_delivery_credential_id_8b1f948b ON public.stewardship_setup_slack_delivery USING btree (credential_id);

-- INDEX: stewardship_setup_source_exchange_attempt_id_2da70c13
CREATE INDEX stewardship_setup_source_exchange_attempt_id_2da70c13 ON public.stewardship_setup_source_exchange USING btree (attempt_id);

-- INDEX: stewardship_setup_source_exchange_correlation_id_d9a5945a
CREATE INDEX stewardship_setup_source_exchange_correlation_id_d9a5945a ON public.stewardship_setup_source_exchange USING btree (correlation_id);

-- INDEX: stewardship_setup_source_exchange_credential_id_64e46e64
CREATE INDEX stewardship_setup_source_exchange_credential_id_64e46e64 ON public.stewardship_setup_source_exchange USING btree (credential_id);

-- INDEX: stewardship_setup_source_exchange_task_id_d4db3fa7
CREATE INDEX stewardship_setup_source_exchange_task_id_d4db3fa7 ON public.stewardship_setup_source_exchange USING btree (task_id);

-- INDEX: stewardship_setup_source_result_correlation_id_59ca121f
CREATE INDEX stewardship_setup_source_result_correlation_id_59ca121f ON public.stewardship_setup_source_result USING btree (correlation_id);

-- INDEX: stewardship_snapshot_address_correlation_id_36679e50
CREATE INDEX stewardship_snapshot_address_correlation_id_36679e50 ON public.stewardship_snapshot_address USING btree (correlation_id);

-- INDEX: stewardship_snapshot_address_payload_id_64eb66d2
CREATE INDEX stewardship_snapshot_address_payload_id_64eb66d2 ON public.stewardship_snapshot_address USING btree (payload_id);

-- INDEX: stewardship_snapshot_address_snapshot_id_8543fae0
CREATE INDEX stewardship_snapshot_address_snapshot_id_8543fae0 ON public.stewardship_snapshot_address USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_contact_correlation_id_7033fd10
CREATE INDEX stewardship_snapshot_contact_correlation_id_7033fd10 ON public.stewardship_snapshot_contact USING btree (correlation_id);

-- INDEX: stewardship_snapshot_contact_payload_id_2289dcae
CREATE INDEX stewardship_snapshot_contact_payload_id_2289dcae ON public.stewardship_snapshot_contact USING btree (payload_id);

-- INDEX: stewardship_snapshot_contact_snapshot_id_edecb2e6
CREATE INDEX stewardship_snapshot_contact_snapshot_id_edecb2e6 ON public.stewardship_snapshot_contact USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_contribution_correlation_id_8ebde758
CREATE INDEX stewardship_snapshot_contribution_correlation_id_8ebde758 ON public.stewardship_snapshot_contribution USING btree (correlation_id);

-- INDEX: stewardship_snapshot_contribution_payload_id_6111da39
CREATE INDEX stewardship_snapshot_contribution_payload_id_6111da39 ON public.stewardship_snapshot_contribution USING btree (payload_id);

-- INDEX: stewardship_snapshot_contribution_snapshot_id_b12a39bf
CREATE INDEX stewardship_snapshot_contribution_snapshot_id_b12a39bf ON public.stewardship_snapshot_contribution USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_family_correlation_id_84d037f3
CREATE INDEX stewardship_snapshot_family_correlation_id_84d037f3 ON public.stewardship_snapshot_family USING btree (correlation_id);

-- INDEX: stewardship_snapshot_family_payload_id_f9e2ecf6
CREATE INDEX stewardship_snapshot_family_payload_id_f9e2ecf6 ON public.stewardship_snapshot_family USING btree (payload_id);

-- INDEX: stewardship_snapshot_family_snapshot_id_823c7910
CREATE INDEX stewardship_snapshot_family_snapshot_id_823c7910 ON public.stewardship_snapshot_family USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_fund_correlation_id_e3f77ad0
CREATE INDEX stewardship_snapshot_fund_correlation_id_e3f77ad0 ON public.stewardship_snapshot_fund USING btree (correlation_id);

-- INDEX: stewardship_snapshot_fund_payload_id_8da27a26
CREATE INDEX stewardship_snapshot_fund_payload_id_8da27a26 ON public.stewardship_snapshot_fund USING btree (payload_id);

-- INDEX: stewardship_snapshot_fund_snapshot_id_a4855404
CREATE INDEX stewardship_snapshot_fund_snapshot_id_a4855404 ON public.stewardship_snapshot_fund USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_member_correlation_id_385cfb60
CREATE INDEX stewardship_snapshot_member_correlation_id_385cfb60 ON public.stewardship_snapshot_member USING btree (correlation_id);

-- INDEX: stewardship_snapshot_member_payload_id_7848b5dc
CREATE INDEX stewardship_snapshot_member_payload_id_7848b5dc ON public.stewardship_snapshot_member USING btree (payload_id);

-- INDEX: stewardship_snapshot_member_snapshot_id_068e177c
CREATE INDEX stewardship_snapshot_member_snapshot_id_068e177c ON public.stewardship_snapshot_member USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_ministry_correlation_id_4fa60aef
CREATE INDEX stewardship_snapshot_ministry_correlation_id_4fa60aef ON public.stewardship_snapshot_ministry USING btree (correlation_id);

-- INDEX: stewardship_snapshot_ministry_payload_id_d0ff7fbb
CREATE INDEX stewardship_snapshot_ministry_payload_id_d0ff7fbb ON public.stewardship_snapshot_ministry USING btree (payload_id);

-- INDEX: stewardship_snapshot_ministry_snapshot_id_b578d514
CREATE INDEX stewardship_snapshot_ministry_snapshot_id_b578d514 ON public.stewardship_snapshot_ministry USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_pledge_correlation_id_a600c58e
CREATE INDEX stewardship_snapshot_pledge_correlation_id_a600c58e ON public.stewardship_snapshot_pledge USING btree (correlation_id);

-- INDEX: stewardship_snapshot_pledge_payload_id_9f3fc629
CREATE INDEX stewardship_snapshot_pledge_payload_id_9f3fc629 ON public.stewardship_snapshot_pledge USING btree (payload_id);

-- INDEX: stewardship_snapshot_pledge_snapshot_id_e8d49637
CREATE INDEX stewardship_snapshot_pledge_snapshot_id_e8d49637 ON public.stewardship_snapshot_pledge USING btree (snapshot_id);

-- INDEX: stewardship_snapshot_roster_correlation_id_ff90e2b6
CREATE INDEX stewardship_snapshot_roster_correlation_id_ff90e2b6 ON public.stewardship_snapshot_roster USING btree (correlation_id);

-- INDEX: stewardship_snapshot_roster_payload_id_5d8be951
CREATE INDEX stewardship_snapshot_roster_payload_id_5d8be951 ON public.stewardship_snapshot_roster USING btree (payload_id);

-- INDEX: stewardship_snapshot_roster_snapshot_id_a0649562
CREATE INDEX stewardship_snapshot_roster_snapshot_id_a0649562 ON public.stewardship_snapshot_roster USING btree (snapshot_id);

-- INDEX: stewardship_source_address_correlation_id_e95e72a7
CREATE INDEX stewardship_source_address_correlation_id_e95e72a7 ON public.stewardship_source_address USING btree (correlation_id);

-- INDEX: stewardship_source_address_owner_key_ce2077e2
CREATE INDEX stewardship_source_address_owner_key_ce2077e2 ON public.stewardship_source_address USING btree (owner_key);

-- INDEX: stewardship_source_address_owner_key_ce2077e2_like
CREATE INDEX stewardship_source_address_owner_key_ce2077e2_like ON public.stewardship_source_address USING btree (owner_key varchar_pattern_ops);

-- INDEX: stewardship_source_compaction_correlation_id_314edbe6
CREATE INDEX stewardship_source_compaction_correlation_id_314edbe6 ON public.stewardship_source_compaction USING btree (correlation_id);

-- INDEX: stewardship_source_compaction_task_id_9d0dbab8
CREATE INDEX stewardship_source_compaction_task_id_9d0dbab8 ON public.stewardship_source_compaction USING btree (task_id);

-- INDEX: stewardship_source_contact_correlation_id_4055bd3c
CREATE INDEX stewardship_source_contact_correlation_id_4055bd3c ON public.stewardship_source_contact USING btree (correlation_id);

-- INDEX: stewardship_source_contact_owner_key_9b4cfd78
CREATE INDEX stewardship_source_contact_owner_key_9b4cfd78 ON public.stewardship_source_contact USING btree (owner_key);

-- INDEX: stewardship_source_contact_owner_key_9b4cfd78_like
CREATE INDEX stewardship_source_contact_owner_key_9b4cfd78_like ON public.stewardship_source_contact USING btree (owner_key varchar_pattern_ops);

-- INDEX: stewardship_source_contribution_correlation_id_48b003fd
CREATE INDEX stewardship_source_contribution_correlation_id_48b003fd ON public.stewardship_source_contribution USING btree (correlation_id);

-- INDEX: stewardship_source_contribution_family_key_12267b16
CREATE INDEX stewardship_source_contribution_family_key_12267b16 ON public.stewardship_source_contribution USING btree (family_key);

-- INDEX: stewardship_source_contribution_family_key_12267b16_like
CREATE INDEX stewardship_source_contribution_family_key_12267b16_like ON public.stewardship_source_contribution USING btree (family_key varchar_pattern_ops);

-- INDEX: stewardship_source_contribution_fund_key_4fa42f2f
CREATE INDEX stewardship_source_contribution_fund_key_4fa42f2f ON public.stewardship_source_contribution USING btree (fund_key);

-- INDEX: stewardship_source_contribution_fund_key_4fa42f2f_like
CREATE INDEX stewardship_source_contribution_fund_key_4fa42f2f_like ON public.stewardship_source_contribution USING btree (fund_key varchar_pattern_ops);

-- INDEX: stewardship_source_current_correlation_id_f6db7b8e
CREATE INDEX stewardship_source_current_correlation_id_f6db7b8e ON public.stewardship_source_current USING btree (correlation_id);

-- INDEX: stewardship_source_family_correlation_id_48f29ea9
CREATE INDEX stewardship_source_family_correlation_id_48f29ea9 ON public.stewardship_source_family USING btree (correlation_id);

-- INDEX: stewardship_source_fund_correlation_id_c4e1b600
CREATE INDEX stewardship_source_fund_correlation_id_c4e1b600 ON public.stewardship_source_fund USING btree (correlation_id);

-- INDEX: stewardship_source_lease_correlation_id_bbb09f19
CREATE INDEX stewardship_source_lease_correlation_id_bbb09f19 ON public.stewardship_source_lease USING btree (correlation_id);

-- INDEX: stewardship_source_lease_owner_id_d199382b
CREATE INDEX stewardship_source_lease_owner_id_d199382b ON public.stewardship_source_lease USING btree (owner_id);

-- INDEX: stewardship_source_member_correlation_id_fba76a1e
CREATE INDEX stewardship_source_member_correlation_id_fba76a1e ON public.stewardship_source_member USING btree (correlation_id);

-- INDEX: stewardship_source_member_family_key_f2698495
CREATE INDEX stewardship_source_member_family_key_f2698495 ON public.stewardship_source_member USING btree (family_key);

-- INDEX: stewardship_source_member_family_key_f2698495_like
CREATE INDEX stewardship_source_member_family_key_f2698495_like ON public.stewardship_source_member USING btree (family_key varchar_pattern_ops);

-- INDEX: stewardship_source_ministry_correlation_id_308b6a44
CREATE INDEX stewardship_source_ministry_correlation_id_308b6a44 ON public.stewardship_source_ministry USING btree (correlation_id);

-- INDEX: stewardship_source_pin_correlation_id_3248e773
CREATE INDEX stewardship_source_pin_correlation_id_3248e773 ON public.stewardship_source_pin USING btree (correlation_id);

-- INDEX: stewardship_source_pin_snapshot_id_c637cd81
CREATE INDEX stewardship_source_pin_snapshot_id_c637cd81 ON public.stewardship_source_pin USING btree (snapshot_id);

-- INDEX: stewardship_source_pledge_correlation_id_530f7020
CREATE INDEX stewardship_source_pledge_correlation_id_530f7020 ON public.stewardship_source_pledge USING btree (correlation_id);

-- INDEX: stewardship_source_pledge_family_key_9b11a755
CREATE INDEX stewardship_source_pledge_family_key_9b11a755 ON public.stewardship_source_pledge USING btree (family_key);

-- INDEX: stewardship_source_pledge_family_key_9b11a755_like
CREATE INDEX stewardship_source_pledge_family_key_9b11a755_like ON public.stewardship_source_pledge USING btree (family_key varchar_pattern_ops);

-- INDEX: stewardship_source_pledge_fund_key_87ae71eb
CREATE INDEX stewardship_source_pledge_fund_key_87ae71eb ON public.stewardship_source_pledge USING btree (fund_key);

-- INDEX: stewardship_source_pledge_fund_key_87ae71eb_like
CREATE INDEX stewardship_source_pledge_fund_key_87ae71eb_like ON public.stewardship_source_pledge USING btree (fund_key varchar_pattern_ops);

-- INDEX: stewardship_source_refresh_attempt_configuration_id_0e9ed759
CREATE INDEX stewardship_source_refresh_attempt_configuration_id_0e9ed759 ON public.stewardship_source_refresh_attempt USING btree (configuration_id);

-- INDEX: stewardship_source_refresh_attempt_correlation_id_c4f2080e
CREATE INDEX stewardship_source_refresh_attempt_correlation_id_c4f2080e ON public.stewardship_source_refresh_attempt USING btree (correlation_id);

-- INDEX: stewardship_source_refresh_attempt_request_id_b2100202
CREATE INDEX stewardship_source_refresh_attempt_request_id_b2100202 ON public.stewardship_source_refresh_attempt USING btree (request_id);

-- INDEX: stewardship_source_refresh_attempt_task_id_ef7e4924
CREATE INDEX stewardship_source_refresh_attempt_task_id_ef7e4924 ON public.stewardship_source_refresh_attempt USING btree (task_id);

-- INDEX: stewardship_source_refresh_command_correlation_id_68c0c0f6
CREATE INDEX stewardship_source_refresh_command_correlation_id_68c0c0f6 ON public.stewardship_source_refresh_command USING btree (correlation_id);

-- INDEX: stewardship_source_refresh_command_request_id_48122291
CREATE INDEX stewardship_source_refresh_command_request_id_48122291 ON public.stewardship_source_refresh_command USING btree (request_id);

-- INDEX: stewardship_source_refresh_fallback_attempt_id_ba35c2ee
CREATE INDEX stewardship_source_refresh_fallback_attempt_id_ba35c2ee ON public.stewardship_source_refresh_fallback USING btree (attempt_id);

-- INDEX: stewardship_source_refresh_fallback_correlation_id_a2fabeb1
CREATE INDEX stewardship_source_refresh_fallback_correlation_id_a2fabeb1 ON public.stewardship_source_refresh_fallback USING btree (correlation_id);

-- INDEX: stewardship_source_refresh_fallback_task_id_c6dcabe9
CREATE INDEX stewardship_source_refresh_fallback_task_id_c6dcabe9 ON public.stewardship_source_refresh_fallback USING btree (task_id);

-- INDEX: stewardship_source_refresh_request_campaign_id_44d23ffc
CREATE INDEX stewardship_source_refresh_request_campaign_id_44d23ffc ON public.stewardship_source_refresh_request USING btree (campaign_id);

-- INDEX: stewardship_source_refresh_request_configuration_id_3e685a4b
CREATE INDEX stewardship_source_refresh_request_configuration_id_3e685a4b ON public.stewardship_source_refresh_request USING btree (configuration_id);

-- INDEX: stewardship_source_refresh_request_correlation_id_f242758e
CREATE INDEX stewardship_source_refresh_request_correlation_id_f242758e ON public.stewardship_source_refresh_request USING btree (correlation_id);

-- INDEX: stewardship_source_refresh_tick_configuration_id_0227e5f3
CREATE INDEX stewardship_source_refresh_tick_configuration_id_0227e5f3 ON public.stewardship_source_refresh_tick USING btree (configuration_id);

-- INDEX: stewardship_source_refresh_tick_correlation_id_b01a8295
CREATE INDEX stewardship_source_refresh_tick_correlation_id_b01a8295 ON public.stewardship_source_refresh_tick USING btree (correlation_id);

-- INDEX: stewardship_source_refresh_tick_slot_key_b2a02c0c_like
CREATE INDEX stewardship_source_refresh_tick_slot_key_b2a02c0c_like ON public.stewardship_source_refresh_tick USING btree (slot_key varchar_pattern_ops);

-- INDEX: stewardship_source_roster_correlation_id_816095ef
CREATE INDEX stewardship_source_roster_correlation_id_816095ef ON public.stewardship_source_roster USING btree (correlation_id);

-- INDEX: stewardship_source_roster_member_key_3431f37d
CREATE INDEX stewardship_source_roster_member_key_3431f37d ON public.stewardship_source_roster USING btree (member_key);

-- INDEX: stewardship_source_roster_member_key_3431f37d_like
CREATE INDEX stewardship_source_roster_member_key_3431f37d_like ON public.stewardship_source_roster USING btree (member_key varchar_pattern_ops);

-- INDEX: stewardship_source_roster_ministry_key_66ab31dc
CREATE INDEX stewardship_source_roster_ministry_key_66ab31dc ON public.stewardship_source_roster USING btree (ministry_key);

-- INDEX: stewardship_source_roster_ministry_key_66ab31dc_like
CREATE INDEX stewardship_source_roster_ministry_key_66ab31dc_like ON public.stewardship_source_roster USING btree (ministry_key varchar_pattern_ops);

-- INDEX: stewardship_source_snapshot_base_id_5be645dc
CREATE INDEX stewardship_source_snapshot_base_id_5be645dc ON public.stewardship_source_snapshot USING btree (base_id);

-- INDEX: stewardship_source_snapshot_correlation_id_914f0380
CREATE INDEX stewardship_source_snapshot_correlation_id_914f0380 ON public.stewardship_source_snapshot USING btree (correlation_id);

-- INDEX: stewardship_source_snapshot_promoted_at_cf97d2f6
CREATE INDEX stewardship_source_snapshot_promoted_at_cf97d2f6 ON public.stewardship_source_snapshot USING btree (promoted_at);

-- INDEX: stewardship_source_snapshot_task_id_81d89e02
CREATE INDEX stewardship_source_snapshot_task_id_81d89e02 ON public.stewardship_source_snapshot USING btree (task_id);

-- INDEX: stewardship_system_configu_active_configuration_id_678237b7
CREATE INDEX stewardship_system_configu_active_configuration_id_678237b7 ON public.stewardship_system_configuration USING btree (active_configuration_id);

-- INDEX: stewardship_system_configuration_correlation_id_070187c3
CREATE INDEX stewardship_system_configuration_correlation_id_070187c3 ON public.stewardship_system_configuration USING btree (correlation_id);

-- INDEX: stewardship_system_configuration_current_campaign_id_0e964cca
CREATE INDEX stewardship_system_configuration_current_campaign_id_0e964cca ON public.stewardship_system_configuration USING btree (current_campaign_id);

-- INDEX: stewardship_task_event_correlation_id_a85d204a
CREATE INDEX stewardship_task_event_correlation_id_a85d204a ON public.stewardship_task_event USING btree (correlation_id);

-- INDEX: stewardship_task_event_run_id_36a778b8
CREATE INDEX stewardship_task_event_run_id_36a778b8 ON public.stewardship_task_event USING btree (run_id);

-- INDEX: stewardship_task_run_correlation_id_1b826333
CREATE INDEX stewardship_task_run_correlation_id_1b826333 ON public.stewardship_task_run USING btree (correlation_id);

-- INDEX: stewardship_task_run_parent_id_0a8fdab8
CREATE INDEX stewardship_task_run_parent_id_0a8fdab8 ON public.stewardship_task_run USING btree (parent_id);

-- INDEX: stewardship_task_run_root_id_f9d3f773
CREATE INDEX stewardship_task_run_root_id_f9d3f773 ON public.stewardship_task_run USING btree (root_id);

-- INDEX: system_configuration_singleton
CREATE UNIQUE INDEX system_configuration_singleton ON public.stewardship_system_configuration USING btree ((1));

-- INDEX: task_due
CREATE INDEX task_due ON public.stewardship_task_run USING btree (state, not_before);

-- INDEX: task_execution_key
CREATE UNIQUE INDEX task_execution_key ON public.stewardship_task_run USING btree (task_type, idempotency_key) WHERE (idempotency_key IS NOT NULL);

-- INDEX: task_lease
CREATE INDEX task_lease ON public.stewardship_task_run USING btree (state, lease_expires_at);

-- INDEX: task_one_nonterminal
CREATE UNIQUE INDEX task_one_nonterminal ON public.stewardship_task_run USING btree (root_id) WHERE ((state)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'retry_wait'::character varying, 'abandoned'::character varying])::text[]));

-- INDEX: task_retry_command
CREATE UNIQUE INDEX task_retry_command ON public.stewardship_task_run USING btree (root_id, retry_command_id) WHERE (retry_command_id IS NOT NULL);

-- TRIGGER: stewardship_setup_draft_section aaa_setup_completion_cleanup
CREATE TRIGGER aaa_setup_completion_cleanup BEFORE UPDATE ON public.stewardship_setup_draft_section FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_cleanup_write_v1();

-- TRIGGER: stewardship_setup_mail_delivery aaa_setup_completion_cleanup
CREATE TRIGGER aaa_setup_completion_cleanup BEFORE UPDATE ON public.stewardship_setup_mail_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_cleanup_write_v1();

-- TRIGGER: stewardship_setup_mail_exchange aaa_setup_completion_cleanup
CREATE TRIGGER aaa_setup_completion_cleanup BEFORE UPDATE ON public.stewardship_setup_mail_exchange FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_cleanup_write_v1();

-- TRIGGER: stewardship_setup_sealed_credential aaa_setup_completion_cleanup
CREATE TRIGGER aaa_setup_completion_cleanup BEFORE UPDATE ON public.stewardship_setup_sealed_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_cleanup_write_v1();

-- TRIGGER: stewardship_setup_source_exchange aaa_setup_completion_cleanup
CREATE TRIGGER aaa_setup_completion_cleanup BEFORE UPDATE ON public.stewardship_setup_source_exchange FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_cleanup_write_v1();

-- TRIGGER: stewardship_campaign aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_campaign_boundary aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_campaign_boundary FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_config_checkpoint aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_config_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_occurrence_transition aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_occurrence_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_policy_epoch aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_policy_epoch FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_policy_security_event aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_policy_security_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_schedule_definition aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_schedule_definition FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_schedule_occurrence aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_schedule_selection aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_schedule_selection FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_system_configuration aaa_setup_completion_write
CREATE TRIGGER aaa_setup_completion_write BEFORE INSERT OR UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_write_v1();

-- TRIGGER: stewardship_config_activation aaa_stewardship_activation_global_v1
CREATE TRIGGER aaa_stewardship_activation_global_v1 BEFORE INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_activation_global_v1();

-- TRIGGER: stewardship_config_activation aab_stewardship_aborted_activation_v1
CREATE TRIGGER aab_stewardship_aborted_activation_v1 BEFORE INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_aborted_activation_v1();

-- TRIGGER: stewardship_config_activation aab_stewardship_setup_activation_owner
CREATE TRIGGER aab_stewardship_setup_activation_owner BEFORE INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_activation_owner_v1();

-- TRIGGER: stewardship_secret_request aab_stewardship_setup_install_progress
CREATE TRIGGER aab_stewardship_setup_install_progress BEFORE UPDATE ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_install_progress_v1();

-- TRIGGER: stewardship_secret_request aac_stewardship_setup_initial_hold
CREATE TRIGGER aac_stewardship_setup_initial_hold BEFORE UPDATE ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_initial_hold_v1();

-- TRIGGER: stewardship_fact_compaction fact_compaction_guard
CREATE TRIGGER fact_compaction_guard BEFORE INSERT ON public.stewardship_fact_compaction FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_compaction_guard();

-- TRIGGER: stewardship_fact_compaction fact_compaction_pair_guard
CREATE CONSTRAINT TRIGGER fact_compaction_pair_guard AFTER INSERT ON public.stewardship_fact_compaction DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_compaction_pair_guard();

-- TRIGGER: stewardship_daily_fact fact_day_guard
CREATE TRIGGER fact_day_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_daily_fact FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_day_guard();

-- TRIGGER: stewardship_daily_fact fact_delete_pair_guard
CREATE CONSTRAINT TRIGGER fact_delete_pair_guard AFTER DELETE ON public.stewardship_daily_fact DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_delete_pair_guard();

-- TRIGGER: stewardship_daily_fact_set fact_deletion_pair_guard
CREATE CONSTRAINT TRIGGER fact_deletion_pair_guard AFTER DELETE ON public.stewardship_daily_fact_set DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_compaction_pair_guard();

-- TRIGGER: stewardship_fact_demand fact_demand_guard
CREATE TRIGGER fact_demand_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_fact_demand FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_demand_guard();

-- TRIGGER: stewardship_fact_pin fact_pin_guard
CREATE TRIGGER fact_pin_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_fact_pin FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_reference_guard();

-- TRIGGER: stewardship_fact_pointer fact_pointer_guard
CREATE TRIGGER fact_pointer_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_fact_pointer FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_reference_guard();

-- TRIGGER: stewardship_daily_fact_set fact_set_guard
CREATE TRIGGER fact_set_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_daily_fact_set FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_set_guard();

-- TRIGGER: stewardship_source_pin fact_source_pin_guard
CREATE TRIGGER fact_source_pin_guard BEFORE DELETE ON public.stewardship_source_pin FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_source_pin_guard();

-- TRIGGER: stewardship_snapshot_address snapshot_address_membership
CREATE TRIGGER snapshot_address_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_address FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_address');

-- TRIGGER: stewardship_snapshot_contact snapshot_contact_membership
CREATE TRIGGER snapshot_contact_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_contact FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_contact');

-- TRIGGER: stewardship_snapshot_contribution snapshot_contribution_membership
CREATE TRIGGER snapshot_contribution_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_contribution FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_contribution');

-- TRIGGER: stewardship_snapshot_family snapshot_family_membership
CREATE TRIGGER snapshot_family_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_family FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_family');

-- TRIGGER: stewardship_snapshot_fund snapshot_fund_membership
CREATE TRIGGER snapshot_fund_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_fund FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_fund');

-- TRIGGER: stewardship_snapshot_member snapshot_member_membership
CREATE TRIGGER snapshot_member_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_member FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_member');

-- TRIGGER: stewardship_snapshot_ministry snapshot_ministry_membership
CREATE TRIGGER snapshot_ministry_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_ministry FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_ministry');

-- TRIGGER: stewardship_snapshot_pledge snapshot_pledge_membership
CREATE TRIGGER snapshot_pledge_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_pledge FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_pledge');

-- TRIGGER: stewardship_snapshot_roster snapshot_roster_membership
CREATE TRIGGER snapshot_roster_membership BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_snapshot_roster FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_membership_guard('stewardship_source_roster');

-- TRIGGER: stewardship_source_address source_address_payload
CREATE TRIGGER source_address_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_address FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard('owner_kind', 'owner_key');

-- TRIGGER: stewardship_source_contact source_contact_payload
CREATE TRIGGER source_contact_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_contact FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard('owner_kind', 'owner_key');

-- TRIGGER: stewardship_source_contribution source_contribution_payload
CREATE TRIGGER source_contribution_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_contribution FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard('family_key', 'fund_key');

-- TRIGGER: stewardship_source_current source_current_guard
CREATE TRIGGER source_current_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_current FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_current_guard();

-- TRIGGER: stewardship_source_family source_family_payload
CREATE TRIGGER source_family_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_family FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard();

-- TRIGGER: stewardship_source_fund source_fund_payload
CREATE TRIGGER source_fund_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_fund FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard();

-- TRIGGER: stewardship_source_member source_member_payload
CREATE TRIGGER source_member_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_member FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard('family_key');

-- TRIGGER: stewardship_source_ministry source_ministry_payload
CREATE TRIGGER source_ministry_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_ministry FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard();

-- TRIGGER: stewardship_source_pin source_pin_guard
CREATE TRIGGER source_pin_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_pin FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_pin_guard();

-- TRIGGER: stewardship_source_pledge source_pledge_payload
CREATE TRIGGER source_pledge_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_pledge FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard('family_key', 'fund_key');

-- TRIGGER: stewardship_source_snapshot source_promotion_pair_guard
CREATE CONSTRAINT TRIGGER source_promotion_pair_guard AFTER UPDATE ON public.stewardship_source_snapshot DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_promotion_pair_guard();

-- TRIGGER: stewardship_source_roster source_roster_payload
CREATE TRIGGER source_roster_payload BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_roster FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_payload_guard('member_key', 'ministry_key');

-- TRIGGER: stewardship_source_snapshot source_snapshot_guard
CREATE TRIGGER source_snapshot_guard BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_snapshot FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_snapshot_guard();

-- TRIGGER: stewardship_activation_catchup stewardship_activation_catchup_mutable_guard_v1
CREATE TRIGGER stewardship_activation_catchup_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_activation_catchup FOR EACH ROW EXECUTE FUNCTION public.stewardship_activation_catchup_mutable_v1();

-- TRIGGER: stewardship_config_activation stewardship_activation_effects_v1
CREATE TRIGGER stewardship_activation_effects_v1 AFTER INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_activation_effects_v1();

-- TRIGGER: stewardship_config_activation stewardship_activation_guard_v1
CREATE TRIGGER stewardship_activation_guard_v1 BEFORE INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_activation_guard_v1();

-- TRIGGER: stewardship_address_grant stewardship_address_grant_immutable_guard_v1
CREATE TRIGGER stewardship_address_grant_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_address_grant FOR EACH ROW EXECUTE FUNCTION public.stewardship_address_grant_immutable_v1();

-- TRIGGER: stewardship_address_rule stewardship_address_rule_immutable_guard_v1
CREATE TRIGGER stewardship_address_rule_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_address_rule FOR EACH ROW EXECUTE FUNCTION public.stewardship_address_rule_immutable_v1();

-- TRIGGER: stewardship_admin_revocation stewardship_admin_revocation_immutable_guard_v1
CREATE TRIGGER stewardship_admin_revocation_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_admin_revocation FOR EACH ROW EXECUTE FUNCTION public.stewardship_admin_revocation_immutable_v1();

-- TRIGGER: stewardship_applied_integration stewardship_applied_integration_immutable_guard_v1
CREATE TRIGGER stewardship_applied_integration_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_applied_integration FOR EACH ROW EXECUTE FUNCTION public.stewardship_applied_integration_immutable_v1();

-- TRIGGER: stewardship_assignment_overlay stewardship_assignment_overlay_mutable_guard_v1
CREATE TRIGGER stewardship_assignment_overlay_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_assignment_overlay FOR EACH ROW EXECUTE FUNCTION public.stewardship_assignment_overlay_mutable_v1();

-- TRIGGER: stewardship_audit_context stewardship_audit_context_immutable_guard_v1
CREATE TRIGGER stewardship_audit_context_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_audit_context FOR EACH ROW EXECUTE FUNCTION public.stewardship_audit_context_immutable_v1();

-- TRIGGER: stewardship_audit_event stewardship_audit_event_immutable_guard_v1
CREATE TRIGGER stewardship_audit_event_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_audit_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_audit_event_immutable_v1();

-- TRIGGER: stewardship_audit_event stewardship_audit_ownership_v1
CREATE TRIGGER stewardship_audit_ownership_v1 BEFORE INSERT ON public.stewardship_audit_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_audit_ownership_v1();

-- TRIGGER: stewardship_auth_incident stewardship_auth_incident_mutable_guard_v1
CREATE TRIGGER stewardship_auth_incident_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_auth_incident FOR EACH ROW EXECUTE FUNCTION public.stewardship_auth_incident_mutable_v1();

-- TRIGGER: stewardship_configuration_version stewardship_bootstrap_empty_database
CREATE TRIGGER stewardship_bootstrap_empty_database BEFORE INSERT ON public.stewardship_configuration_version FOR EACH ROW EXECUTE FUNCTION public.stewardship_bootstrap_empty_database();

-- TRIGGER: stewardship_applied_integration stewardship_bootstrap_projection_v1
CREATE TRIGGER stewardship_bootstrap_projection_v1 BEFORE INSERT ON public.stewardship_applied_integration FOR EACH ROW EXECUTE FUNCTION public.stewardship_bootstrap_projection_v1();

-- TRIGGER: stewardship_parish stewardship_bootstrap_projection_v1
CREATE TRIGGER stewardship_bootstrap_projection_v1 BEFORE INSERT ON public.stewardship_parish FOR EACH ROW EXECUTE FUNCTION public.stewardship_bootstrap_projection_v1();

-- TRIGGER: stewardship_configuration_version stewardship_bootstrap_shape_v1
CREATE TRIGGER stewardship_bootstrap_shape_v1 BEFORE INSERT ON public.stewardship_configuration_version FOR EACH ROW EXECUTE FUNCTION public.stewardship_bootstrap_shape_v1();

-- TRIGGER: stewardship_campaign_boundary stewardship_boundary_audit_v1
CREATE TRIGGER stewardship_boundary_audit_v1 AFTER UPDATE ON public.stewardship_campaign_boundary FOR EACH ROW EXECUTE FUNCTION public.stewardship_boundary_audit_v1();

-- TRIGGER: stewardship_campaign_boundary stewardship_boundary_guard_v1
CREATE TRIGGER stewardship_boundary_guard_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_campaign_boundary FOR EACH ROW EXECUTE FUNCTION public.stewardship_boundary_guard_v1();

-- TRIGGER: stewardship_branding_asset stewardship_branding_asset_immutable_guard_v1
CREATE TRIGGER stewardship_branding_asset_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_branding_asset FOR EACH ROW EXECUTE FUNCTION public.stewardship_branding_asset_immutable_v1();

-- TRIGGER: stewardship_branding_asset stewardship_branding_asset_insert_v1
CREATE TRIGGER stewardship_branding_asset_insert_v1 BEFORE INSERT ON public.stewardship_branding_asset FOR EACH ROW EXECUTE FUNCTION public.stewardship_branding_asset_insert_v1();

-- TRIGGER: stewardship_branding_bundle stewardship_branding_bundle_guard_v1
CREATE TRIGGER stewardship_branding_bundle_guard_v1 BEFORE INSERT OR UPDATE ON public.stewardship_branding_bundle FOR EACH ROW EXECUTE FUNCTION public.stewardship_branding_bundle_guard_v1();

-- TRIGGER: stewardship_branding_bundle stewardship_branding_bundle_mutable_guard_v1
CREATE TRIGGER stewardship_branding_bundle_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_branding_bundle FOR EACH ROW EXECUTE FUNCTION public.stewardship_branding_bundle_mutable_v1();

-- TRIGGER: stewardship_system_configuration stewardship_campaign_activate_v1
CREATE TRIGGER stewardship_campaign_activate_v1 AFTER UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_activate_v1();

-- TRIGGER: stewardship_campaign_boundary stewardship_campaign_boundary_mutable_guard_v1
CREATE TRIGGER stewardship_campaign_boundary_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_campaign_boundary FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_boundary_mutable_v1();

-- TRIGGER: stewardship_configuration_version stewardship_campaign_complete_v1
CREATE CONSTRAINT TRIGGER stewardship_campaign_complete_v1 AFTER INSERT ON public.stewardship_configuration_version DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_complete_v1();

-- TRIGGER: stewardship_campaign_config_abort stewardship_campaign_config_abort_immutable_guard_v1
CREATE TRIGGER stewardship_campaign_config_abort_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_campaign_config_abort FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_config_abort_immutable_v1();

-- TRIGGER: stewardship_campaign_config_intent stewardship_campaign_config_intent_immutable_guard_v1
CREATE TRIGGER stewardship_campaign_config_intent_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_campaign_config_intent FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_config_intent_immutable_v1();

-- TRIGGER: stewardship_campaign_configuration stewardship_campaign_configuration_immutable_guard_v1
CREATE TRIGGER stewardship_campaign_configuration_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_campaign_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_configuration_immutable_v1();

-- TRIGGER: stewardship_campaign_control stewardship_campaign_control_immutable_guard_v1
CREATE TRIGGER stewardship_campaign_control_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_campaign_control FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_control_immutable_v1();

-- TRIGGER: stewardship_campaign_credentials stewardship_campaign_credentials_mutable_guard_v1
CREATE TRIGGER stewardship_campaign_credentials_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_campaign_credentials FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_credentials_mutable_v1();

-- TRIGGER: stewardship_system_configuration stewardship_campaign_end_admission_v1
CREATE TRIGGER stewardship_campaign_end_admission_v1 BEFORE UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_end_admission_v1();

-- TRIGGER: stewardship_campaign_config_intent stewardship_campaign_intent_v1
CREATE TRIGGER stewardship_campaign_intent_v1 BEFORE INSERT ON public.stewardship_campaign_config_intent FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_intent_v1();

-- TRIGGER: stewardship_campaign_mail_test stewardship_campaign_mail_admission
CREATE TRIGGER stewardship_campaign_mail_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_campaign_mail_test FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_mail_guard_v1();

-- TRIGGER: stewardship_campaign_mail_test stewardship_campaign_mail_audit
CREATE TRIGGER stewardship_campaign_mail_audit AFTER INSERT OR UPDATE ON public.stewardship_campaign_mail_test FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_mail_audit_v1();

-- TRIGGER: stewardship_campaign_mail_test stewardship_campaign_mail_test_mutable_guard_v1
CREATE TRIGGER stewardship_campaign_mail_test_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_campaign_mail_test FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_mail_test_mutable_v1();

-- TRIGGER: stewardship_campaign stewardship_campaign_mutable_guard_v1
CREATE TRIGGER stewardship_campaign_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_mutable_v1();

-- TRIGGER: stewardship_system_configuration stewardship_campaign_pointer_v1
CREATE TRIGGER stewardship_campaign_pointer_v1 BEFORE INSERT OR UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_pointer_v1();

-- TRIGGER: stewardship_campaign_configuration stewardship_campaign_projection_v1
CREATE TRIGGER stewardship_campaign_projection_v1 BEFORE INSERT ON public.stewardship_campaign_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_projection_v1();

-- TRIGGER: stewardship_schedule_revision stewardship_campaign_projection_v1
CREATE TRIGGER stewardship_campaign_projection_v1 BEFORE INSERT ON public.stewardship_schedule_revision FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_projection_v1();

-- TRIGGER: stewardship_campaign stewardship_campaign_runtime_v1
CREATE TRIGGER stewardship_campaign_runtime_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_runtime_v1();

-- TRIGGER: stewardship_campaign_transition stewardship_campaign_transition_effect_v1
CREATE TRIGGER stewardship_campaign_transition_effect_v1 AFTER INSERT ON public.stewardship_campaign_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_transition_effect_v1();

-- TRIGGER: stewardship_campaign_transition stewardship_campaign_transition_immutable_guard_v1
CREATE TRIGGER stewardship_campaign_transition_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_campaign_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_transition_immutable_v1();

-- TRIGGER: stewardship_campaign_transition stewardship_campaign_transition_v1
CREATE TRIGGER stewardship_campaign_transition_v1 BEFORE INSERT ON public.stewardship_campaign_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_transition_v1();

-- TRIGGER: stewardship_campaign_work_gate stewardship_campaign_work_gate_mutable_guard_v1
CREATE TRIGGER stewardship_campaign_work_gate_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_campaign_work_gate FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_work_gate_mutable_v1();

-- TRIGGER: stewardship_catchup_checkpoint stewardship_catchup_checkpoint_immutable_guard_v1
CREATE TRIGGER stewardship_catchup_checkpoint_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_catchup_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_catchup_checkpoint_immutable_v1();

-- TRIGGER: stewardship_catchup_failure stewardship_catchup_failure_effect_v1
CREATE TRIGGER stewardship_catchup_failure_effect_v1 AFTER INSERT ON public.stewardship_catchup_failure FOR EACH ROW EXECUTE FUNCTION public.stewardship_catchup_failure_effect_v1();

-- TRIGGER: stewardship_catchup_failure stewardship_catchup_failure_guard_v1
CREATE TRIGGER stewardship_catchup_failure_guard_v1 BEFORE INSERT ON public.stewardship_catchup_failure FOR EACH ROW EXECUTE FUNCTION public.stewardship_catchup_failure_guard_v1();

-- TRIGGER: stewardship_catchup_failure stewardship_catchup_failure_immutable_guard_v1
CREATE TRIGGER stewardship_catchup_failure_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_catchup_failure FOR EACH ROW EXECUTE FUNCTION public.stewardship_catchup_failure_immutable_v1();

-- TRIGGER: stewardship_activation_catchup stewardship_catchup_guard_v1
CREATE TRIGGER stewardship_catchup_guard_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_activation_catchup FOR EACH ROW EXECUTE FUNCTION public.stewardship_catchup_guard_v1();

-- TRIGGER: stewardship_chair_reconciliation stewardship_chair_reconciliation_effects
CREATE TRIGGER stewardship_chair_reconciliation_effects AFTER INSERT ON public.stewardship_chair_reconciliation FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_reconciliation_effects_v1();

-- TRIGGER: stewardship_chair_reconciliation stewardship_chair_reconciliation_immutable_guard_v1
CREATE TRIGGER stewardship_chair_reconciliation_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_chair_reconciliation FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_reconciliation_immutable_v1();

-- TRIGGER: stewardship_chair_reconciliation stewardship_chair_reconciliation_insert
CREATE TRIGGER stewardship_chair_reconciliation_insert BEFORE INSERT ON public.stewardship_chair_reconciliation FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_reconciliation_guard_v1();

-- TRIGGER: stewardship_chair_review stewardship_chair_review_mutable_guard_v1
CREATE TRIGGER stewardship_chair_review_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_chair_review FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_review_mutable_v1();

-- TRIGGER: stewardship_chair_review stewardship_chair_review_owner
CREATE TRIGGER stewardship_chair_review_owner BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_chair_review FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_review_owner_v1();

-- TRIGGER: stewardship_chair_seed_evidence stewardship_chair_seed_evidence_immutable_guard_v1
CREATE TRIGGER stewardship_chair_seed_evidence_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_chair_seed_evidence FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_seed_evidence_immutable_v1();

-- TRIGGER: stewardship_chair_seed_evidence stewardship_chair_seed_evidence_insert
CREATE TRIGGER stewardship_chair_seed_evidence_insert BEFORE INSERT ON public.stewardship_chair_seed_evidence FOR EACH ROW EXECUTE FUNCTION public.stewardship_chair_seed_evidence_guard_v1();

-- TRIGGER: stewardship_catchup_checkpoint stewardship_checkpoint_effect_v1
CREATE TRIGGER stewardship_checkpoint_effect_v1 AFTER INSERT ON public.stewardship_catchup_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_checkpoint_effect_v1();

-- TRIGGER: stewardship_catchup_checkpoint stewardship_checkpoint_guard_v1
CREATE TRIGGER stewardship_checkpoint_guard_v1 BEFORE INSERT ON public.stewardship_catchup_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_checkpoint_guard_v1();

-- TRIGGER: stewardship_secret_request stewardship_complete_consumers_v1
CREATE TRIGGER stewardship_complete_consumers_v1 BEFORE INSERT ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_complete_consumers_v1();

-- TRIGGER: stewardship_config_activation stewardship_config_activation_immutable_guard_v1
CREATE TRIGGER stewardship_config_activation_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_config_activation_immutable_v1();

-- TRIGGER: stewardship_config_checkpoint stewardship_config_checkpoint_immutable_guard_v1
CREATE TRIGGER stewardship_config_checkpoint_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_config_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_config_checkpoint_immutable_v1();

-- TRIGGER: stewardship_config_request stewardship_config_request_immutable_guard_v1
CREATE TRIGGER stewardship_config_request_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_config_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_config_request_immutable_v1();

-- TRIGGER: stewardship_configuration_version stewardship_configuration_version_immutable_guard_v1
CREATE TRIGGER stewardship_configuration_version_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_configuration_version FOR EACH ROW EXECUTE FUNCTION public.stewardship_configuration_version_immutable_v1();

-- TRIGGER: stewardship_configuration_version stewardship_content_complete_v1
CREATE CONSTRAINT TRIGGER stewardship_content_complete_v1 AFTER INSERT ON public.stewardship_configuration_version DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_content_complete_v1();

-- TRIGGER: stewardship_content_version stewardship_content_projection_v1
CREATE TRIGGER stewardship_content_projection_v1 BEFORE INSERT ON public.stewardship_content_version FOR EACH ROW EXECUTE FUNCTION public.stewardship_content_projection_v1();

-- TRIGGER: stewardship_content_version stewardship_content_version_immutable_guard_v1
CREATE TRIGGER stewardship_content_version_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_content_version FOR EACH ROW EXECUTE FUNCTION public.stewardship_content_version_immutable_v1();

-- TRIGGER: stewardship_campaign_control stewardship_control_effect_v1
CREATE TRIGGER stewardship_control_effect_v1 AFTER INSERT ON public.stewardship_campaign_control FOR EACH ROW EXECUTE FUNCTION public.stewardship_control_effect_v1();

-- TRIGGER: stewardship_campaign_control stewardship_control_guard_v1
CREATE TRIGGER stewardship_control_guard_v1 BEFORE INSERT ON public.stewardship_campaign_control FOR EACH ROW EXECUTE FUNCTION public.stewardship_control_guard_v1();

-- TRIGGER: stewardship_campaign_control stewardship_control_time_v1
CREATE TRIGGER stewardship_control_time_v1 BEFORE INSERT ON public.stewardship_campaign_control FOR EACH ROW EXECUTE FUNCTION public.stewardship_control_time_v1();

-- TRIGGER: stewardship_credential_consumer_ack stewardship_credential_ack_guard_v1
CREATE TRIGGER stewardship_credential_ack_guard_v1 BEFORE INSERT ON public.stewardship_credential_consumer_ack FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_ack_guard_v1();

-- TRIGGER: stewardship_credential_consumer_ack stewardship_credential_consumer_ack_immutable_guard_v1
CREATE TRIGGER stewardship_credential_consumer_ack_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_credential_consumer_ack FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_consumer_ack_immutable_v1();

-- TRIGGER: stewardship_credential_deployment stewardship_credential_deployment_mutable_guard_v1
CREATE TRIGGER stewardship_credential_deployment_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_credential_deployment FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_deployment_mutable_v1();

-- TRIGGER: stewardship_credential_key_state stewardship_credential_key_state_mutable_guard_v1
CREATE TRIGGER stewardship_credential_key_state_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_credential_key_state FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_key_state_mutable_v1();

-- TRIGGER: stewardship_campaign_credentials stewardship_credential_scope_v1
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON public.stewardship_campaign_credentials FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_scope_v1();

-- TRIGGER: stewardship_family_code_mac stewardship_credential_scope_v1
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_code_mac FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_scope_v1();

-- TRIGGER: stewardship_family_session stewardship_credential_scope_v1
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_scope_v1();

-- TRIGGER: stewardship_family_token stewardship_credential_scope_v1
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_token FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_scope_v1();

-- TRIGGER: stewardship_rehearsal_code_mac stewardship_credential_scope_v1
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON public.stewardship_rehearsal_code_mac FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_scope_v1();

-- TRIGGER: stewardship_rehearsal_credential stewardship_credential_scope_v1
CREATE TRIGGER stewardship_credential_scope_v1 BEFORE INSERT OR UPDATE ON public.stewardship_rehearsal_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_credential_scope_v1();

-- TRIGGER: stewardship_daily_fact_set stewardship_daily_fact_set_mutable_guard_v1
CREATE TRIGGER stewardship_daily_fact_set_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_daily_fact_set FOR EACH ROW EXECUTE FUNCTION public.stewardship_daily_fact_set_mutable_v1();

-- TRIGGER: stewardship_domain_rule stewardship_domain_rule_immutable_guard_v1
CREATE TRIGGER stewardship_domain_rule_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_domain_rule FOR EACH ROW EXECUTE FUNCTION public.stewardship_domain_rule_immutable_v1();

-- TRIGGER: stewardship_download_policy stewardship_download_budget_v1
CREATE TRIGGER stewardship_download_budget_v1 BEFORE DELETE OR UPDATE ON public.stewardship_download_policy FOR EACH ROW EXECUTE FUNCTION public.stewardship_download_budget_v1();

-- TRIGGER: stewardship_campaign_config_abort stewardship_exceptional_abort_v1
CREATE TRIGGER stewardship_exceptional_abort_v1 BEFORE INSERT ON public.stewardship_campaign_config_abort FOR EACH ROW EXECUTE FUNCTION public.stewardship_exceptional_abort_v1();

-- TRIGGER: stewardship_fact_compaction stewardship_fact_compaction_immutable_guard_v1
CREATE TRIGGER stewardship_fact_compaction_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_fact_compaction FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_compaction_immutable_v1();

-- TRIGGER: stewardship_fact_demand stewardship_fact_demand_mutable_guard_v1
CREATE TRIGGER stewardship_fact_demand_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_fact_demand FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_demand_mutable_v1();

-- TRIGGER: stewardship_fact_pin stewardship_fact_pin_mutable_guard_v1
CREATE TRIGGER stewardship_fact_pin_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_fact_pin FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_pin_mutable_v1();

-- TRIGGER: stewardship_fact_pointer stewardship_fact_pointer_mutable_guard_v1
CREATE TRIGGER stewardship_fact_pointer_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_fact_pointer FOR EACH ROW EXECUTE FUNCTION public.stewardship_fact_pointer_mutable_v1();

-- TRIGGER: stewardship_family_campaign stewardship_family_campaign_mutable_guard_v1
CREATE TRIGGER stewardship_family_campaign_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_family_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_campaign_mutable_v1();

-- TRIGGER: stewardship_family_code_mac stewardship_family_code_mac_immutable_guard_v1
CREATE TRIGGER stewardship_family_code_mac_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_family_code_mac FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_code_mac_immutable_v1();

-- TRIGGER: stewardship_family_campaign stewardship_family_cohort_v1
CREATE TRIGGER stewardship_family_cohort_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_cohort_v1();

-- TRIGGER: stewardship_family_eligibility stewardship_family_eligibility_immutable_guard_v1
CREATE TRIGGER stewardship_family_eligibility_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_family_eligibility FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_eligibility_immutable_v1();

-- TRIGGER: stewardship_family_campaign stewardship_family_history_v1
CREATE TRIGGER stewardship_family_history_v1 AFTER INSERT OR UPDATE ON public.stewardship_family_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_history_v1();

-- TRIGGER: stewardship_family_session stewardship_family_presence_v1
CREATE TRIGGER stewardship_family_presence_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_presence_v1();

-- TRIGGER: stewardship_family_session stewardship_family_session_epoch_v1
CREATE TRIGGER stewardship_family_session_epoch_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_session_epoch_v1();

-- TRIGGER: stewardship_family_session stewardship_family_session_mutable_guard_v1
CREATE TRIGGER stewardship_family_session_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_family_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_session_mutable_v1();

-- TRIGGER: stewardship_family_token_generation stewardship_family_token_generation_mutable_guard_v1
CREATE TRIGGER stewardship_family_token_generation_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_family_token_generation FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_token_generation_mutable_v1();

-- TRIGGER: stewardship_family_token stewardship_family_token_mutable_guard_v1
CREATE TRIGGER stewardship_family_token_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_family_token FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_token_mutable_v1();

-- TRIGGER: stewardship_schedule_fulfillment stewardship_fulfillment_guard_v1
CREATE TRIGGER stewardship_fulfillment_guard_v1 BEFORE INSERT ON public.stewardship_schedule_fulfillment FOR EACH ROW EXECUTE FUNCTION public.stewardship_fulfillment_guard_v1();

-- TRIGGER: stewardship_limiter_health stewardship_limiter_health_mutable_guard_v1
CREATE TRIGGER stewardship_limiter_health_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_limiter_health FOR EACH ROW EXECUTE FUNCTION public.stewardship_limiter_health_mutable_v1();

-- TRIGGER: stewardship_config_activation stewardship_ministry_activation_v1
CREATE CONSTRAINT TRIGGER stewardship_ministry_activation_v1 AFTER INSERT ON public.stewardship_config_activation DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_activation_v1();

-- TRIGGER: stewardship_ministry_activity stewardship_ministry_activity_immutable_guard_v1
CREATE TRIGGER stewardship_ministry_activity_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_ministry_activity FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_activity_immutable_v1();

-- TRIGGER: stewardship_ministry_assignment stewardship_ministry_assignment_immutable_guard_v1
CREATE TRIGGER stewardship_ministry_assignment_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_ministry_assignment FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_assignment_immutable_v1();

-- TRIGGER: stewardship_configuration_version stewardship_ministry_complete_v1
CREATE CONSTRAINT TRIGGER stewardship_ministry_complete_v1 AFTER INSERT ON public.stewardship_configuration_version DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_complete_v1();

-- TRIGGER: stewardship_ministry_activity stewardship_ministry_projection_v1
CREATE TRIGGER stewardship_ministry_projection_v1 BEFORE INSERT ON public.stewardship_ministry_activity FOR EACH ROW EXECUTE FUNCTION public.stewardship_ministry_projection_v1();

-- TRIGGER: stewardship_schedule_occurrence stewardship_occurrence_guard_v1
CREATE TRIGGER stewardship_occurrence_guard_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION public.stewardship_occurrence_guard_v1();

-- TRIGGER: stewardship_schedule_occurrence stewardship_occurrence_history_v1
CREATE TRIGGER stewardship_occurrence_history_v1 AFTER INSERT OR UPDATE ON public.stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION public.stewardship_occurrence_history_v1();

-- TRIGGER: stewardship_occurrence_transition stewardship_occurrence_transition_immutable_guard_v1
CREATE TRIGGER stewardship_occurrence_transition_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_occurrence_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_occurrence_transition_immutable_v1();

-- TRIGGER: stewardship_operational_log stewardship_operational_log_immutable_guard_v1
CREATE TRIGGER stewardship_operational_log_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_operational_log FOR EACH ROW EXECUTE FUNCTION public.stewardship_operational_log_immutable_v1();

-- TRIGGER: stewardship_parish stewardship_parish_branding_v1
CREATE TRIGGER stewardship_parish_branding_v1 BEFORE INSERT ON public.stewardship_parish FOR EACH ROW EXECUTE FUNCTION public.stewardship_parish_branding_v1();

-- TRIGGER: stewardship_parish stewardship_parish_immutable_guard_v1
CREATE TRIGGER stewardship_parish_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_parish FOR EACH ROW EXECUTE FUNCTION public.stewardship_parish_immutable_v1();

-- TRIGGER: stewardship_config_activation stewardship_policy_activation_v1
CREATE TRIGGER stewardship_policy_activation_v1 AFTER INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_activation_v1();

-- TRIGGER: stewardship_configuration_version stewardship_policy_complete_v1
CREATE CONSTRAINT TRIGGER stewardship_policy_complete_v1 AFTER INSERT ON public.stewardship_configuration_version DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_complete_v1();

-- TRIGGER: stewardship_policy_epoch stewardship_policy_epoch_immutable_guard_v1
CREATE TRIGGER stewardship_policy_epoch_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_policy_epoch FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_epoch_immutable_v1();

-- TRIGGER: stewardship_address_grant stewardship_policy_projection_v1
CREATE TRIGGER stewardship_policy_projection_v1 BEFORE INSERT ON public.stewardship_address_grant FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_projection_v1();

-- TRIGGER: stewardship_address_rule stewardship_policy_projection_v1
CREATE TRIGGER stewardship_policy_projection_v1 BEFORE INSERT ON public.stewardship_address_rule FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_projection_v1();

-- TRIGGER: stewardship_domain_rule stewardship_policy_projection_v1
CREATE TRIGGER stewardship_policy_projection_v1 BEFORE INSERT ON public.stewardship_domain_rule FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_projection_v1();

-- TRIGGER: stewardship_ministry_assignment stewardship_policy_projection_v1
CREATE TRIGGER stewardship_policy_projection_v1 BEFORE INSERT ON public.stewardship_ministry_assignment FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_projection_v1();

-- TRIGGER: stewardship_policy_security_event stewardship_policy_security_event_immutable_guard_v1
CREATE TRIGGER stewardship_policy_security_event_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_policy_security_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_policy_security_event_immutable_v1();

-- TRIGGER: stewardship_family_campaign stewardship_population_insert_dirty_v1
CREATE TRIGGER stewardship_population_insert_dirty_v1 AFTER INSERT ON public.stewardship_family_campaign REFERENCING NEW TABLE AS new_families FOR EACH STATEMENT EXECUTE FUNCTION public.stewardship_population_insert_dirty_v1();

-- TRIGGER: stewardship_campaign_credentials stewardship_population_manifest_v1
CREATE TRIGGER stewardship_population_manifest_v1 BEFORE INSERT OR UPDATE ON public.stewardship_campaign_credentials FOR EACH ROW EXECUTE FUNCTION public.stewardship_population_manifest_v1();

-- TRIGGER: stewardship_family_campaign stewardship_population_update_dirty_v1
CREATE TRIGGER stewardship_population_update_dirty_v1 AFTER UPDATE ON public.stewardship_family_campaign REFERENCING OLD TABLE AS old_families NEW TABLE AS new_families FOR EACH STATEMENT EXECUTE FUNCTION public.stewardship_population_update_dirty_v1();

-- TRIGGER: stewardship_portal_session stewardship_portal_session_mutable_guard_v1
CREATE TRIGGER stewardship_portal_session_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_portal_session FOR EACH ROW EXECUTE FUNCTION public.stewardship_portal_session_mutable_v1();

-- TRIGGER: stewardship_portal_user stewardship_portal_user_mutable_guard_v1
CREATE TRIGGER stewardship_portal_user_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_portal_user FOR EACH ROW EXECUTE FUNCTION public.stewardship_portal_user_mutable_v1();

-- TRIGGER: stewardship_postclose_resolution stewardship_postclose_guard_v1
CREATE TRIGGER stewardship_postclose_guard_v1 BEFORE INSERT ON public.stewardship_postclose_resolution FOR EACH ROW EXECUTE FUNCTION public.stewardship_postclose_guard_v1();

-- TRIGGER: stewardship_postclose_resolution stewardship_postclose_resolution_immutable_guard_v1
CREATE TRIGGER stewardship_postclose_resolution_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_postclose_resolution FOR EACH ROW EXECUTE FUNCTION public.stewardship_postclose_resolution_immutable_v1();

-- TRIGGER: stewardship_provider_context stewardship_provider_context_immutable_guard_v1
CREATE TRIGGER stewardship_provider_context_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_provider_context FOR EACH ROW EXECUTE FUNCTION public.stewardship_provider_context_immutable_v1();

-- TRIGGER: stewardship_provider_context stewardship_provider_context_insert_v1
CREATE TRIGGER stewardship_provider_context_insert_v1 BEFORE INSERT ON public.stewardship_provider_context FOR EACH ROW EXECUTE FUNCTION public.stewardship_provider_context_insert_v1();

-- TRIGGER: stewardship_public_credential_handoff stewardship_public_credential_handoff_immutable_guard_v1
CREATE TRIGGER stewardship_public_credential_handoff_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_public_credential_handoff FOR EACH ROW EXECUTE FUNCTION public.stewardship_public_credential_handoff_immutable_v1();

-- TRIGGER: stewardship_public_credential_handoff stewardship_public_handoff_insert_v1
CREATE TRIGGER stewardship_public_handoff_insert_v1 BEFORE INSERT ON public.stewardship_public_credential_handoff FOR EACH ROW EXECUTE FUNCTION public.stewardship_public_handoff_insert_v1();

-- TRIGGER: stewardship_config_activation stewardship_recovery_activation_v1
CREATE TRIGGER stewardship_recovery_activation_v1 AFTER INSERT ON public.stewardship_config_activation FOR EACH ROW EXECUTE FUNCTION public.stewardship_recovery_activation_v1();

-- TRIGGER: stewardship_policy_security_event stewardship_recovery_recipient_v1
CREATE TRIGGER stewardship_recovery_recipient_v1 BEFORE INSERT ON public.stewardship_policy_security_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_recovery_recipient_v1();

-- TRIGGER: stewardship_config_request stewardship_recovery_request_v1
CREATE TRIGGER stewardship_recovery_request_v1 BEFORE INSERT ON public.stewardship_config_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_recovery_request_v1();

-- TRIGGER: stewardship_source_refresh_attempt stewardship_refresh_attempt_insert
CREATE TRIGGER stewardship_refresh_attempt_insert BEFORE INSERT ON public.stewardship_source_refresh_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_attempt_guard_v1();

-- TRIGGER: stewardship_source_refresh_command stewardship_refresh_command_insert
CREATE TRIGGER stewardship_refresh_command_insert BEFORE INSERT ON public.stewardship_source_refresh_command FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_command_guard_v1();

-- TRIGGER: stewardship_source_refresh_fallback stewardship_refresh_fallback_insert
CREATE TRIGGER stewardship_refresh_fallback_insert BEFORE INSERT ON public.stewardship_source_refresh_fallback FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_fallback_guard_v1();

-- TRIGGER: stewardship_source_refresh_request stewardship_refresh_request_insert
CREATE TRIGGER stewardship_refresh_request_insert BEFORE INSERT ON public.stewardship_source_refresh_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_request_guard_v1();

-- TRIGGER: stewardship_source_snapshot stewardship_refresh_snapshot_complete
CREATE TRIGGER stewardship_refresh_snapshot_complete BEFORE UPDATE ON public.stewardship_source_snapshot FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_snapshot_completion_v1();

-- TRIGGER: stewardship_source_refresh_tick stewardship_refresh_tick_insert
CREATE TRIGGER stewardship_refresh_tick_insert BEFORE INSERT ON public.stewardship_source_refresh_tick FOR EACH ROW EXECUTE FUNCTION public.stewardship_refresh_tick_guard_v1();

-- TRIGGER: stewardship_rehearsal_code_mac stewardship_rehearsal_code_mac_retention_guard_v1
CREATE TRIGGER stewardship_rehearsal_code_mac_retention_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_rehearsal_code_mac FOR EACH ROW EXECUTE FUNCTION public.stewardship_rehearsal_code_mac_retention_v1();

-- TRIGGER: stewardship_rehearsal_credential stewardship_rehearsal_credential_mutable_guard_v1
CREATE TRIGGER stewardship_rehearsal_credential_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_rehearsal_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_rehearsal_credential_mutable_v1();

-- TRIGGER: stewardship_rehearsal_code_mac stewardship_rehearsal_delete_v1
CREATE TRIGGER stewardship_rehearsal_delete_v1 BEFORE DELETE ON public.stewardship_rehearsal_code_mac FOR EACH ROW EXECUTE FUNCTION public.stewardship_rehearsal_delete_v1();

-- TRIGGER: stewardship_rehearsal_credential stewardship_rehearsal_delete_v1
CREATE TRIGGER stewardship_rehearsal_delete_v1 BEFORE DELETE ON public.stewardship_rehearsal_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_rehearsal_delete_v1();

-- TRIGGER: stewardship_rehearsal_epoch stewardship_rehearsal_epoch_mutable_guard_v1
CREATE TRIGGER stewardship_rehearsal_epoch_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_rehearsal_epoch FOR EACH ROW EXECUTE FUNCTION public.stewardship_rehearsal_epoch_mutable_v1();

-- TRIGGER: stewardship_rehearsal_reservation stewardship_rehearsal_reservation_immutable_guard_v1
CREATE TRIGGER stewardship_rehearsal_reservation_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_rehearsal_reservation FOR EACH ROW EXECUTE FUNCTION public.stewardship_rehearsal_reservation_immutable_v1();

-- TRIGGER: stewardship_config_checkpoint stewardship_request_audit_v1
CREATE TRIGGER stewardship_request_audit_v1 AFTER INSERT ON public.stewardship_config_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_request_audit_v1();

-- TRIGGER: stewardship_config_checkpoint stewardship_request_checkpoint_v2
CREATE TRIGGER stewardship_request_checkpoint_v2 BEFORE INSERT ON public.stewardship_config_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_request_checkpoint_v2();

-- TRIGGER: stewardship_config_request stewardship_request_stage_v1
CREATE TRIGGER stewardship_request_stage_v1 AFTER INSERT ON public.stewardship_config_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_request_stage_v1();

-- TRIGGER: stewardship_restore_delivery_hold stewardship_restore_delivery_hold_mutable_guard_v1
CREATE TRIGGER stewardship_restore_delivery_hold_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_restore_delivery_hold FOR EACH ROW EXECUTE FUNCTION public.stewardship_restore_delivery_hold_mutable_v1();

-- TRIGGER: stewardship_restore_hold_resolution stewardship_restore_hold_resolution_immutable_guard_v1
CREATE TRIGGER stewardship_restore_hold_resolution_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_restore_hold_resolution FOR EACH ROW EXECUTE FUNCTION public.stewardship_restore_hold_resolution_immutable_v1();

-- TRIGGER: stewardship_restore_delivery_hold stewardship_restore_hold_v1
CREATE TRIGGER stewardship_restore_hold_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_restore_delivery_hold FOR EACH ROW EXECUTE FUNCTION public.stewardship_restore_hold_v1();

-- TRIGGER: stewardship_restore_hold_resolution stewardship_restore_resolution_effect_v1
CREATE TRIGGER stewardship_restore_resolution_effect_v1 AFTER INSERT ON public.stewardship_restore_hold_resolution FOR EACH ROW EXECUTE FUNCTION public.stewardship_restore_resolution_effect_v1();

-- TRIGGER: stewardship_restore_hold_resolution stewardship_restore_resolution_v1
CREATE TRIGGER stewardship_restore_resolution_v1 BEFORE INSERT ON public.stewardship_restore_hold_resolution FOR EACH ROW EXECUTE FUNCTION public.stewardship_restore_resolution_v1();

-- TRIGGER: stewardship_system_configuration stewardship_runtime_activation_required_v1
CREATE CONSTRAINT TRIGGER stewardship_runtime_activation_required_v1 AFTER INSERT ON public.stewardship_system_configuration DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_runtime_activation_required_v1();

-- TRIGGER: stewardship_system_configuration stewardship_runtime_guard_v1
CREATE TRIGGER stewardship_runtime_guard_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_runtime_guard_v1();

-- TRIGGER: stewardship_runtime_transition stewardship_runtime_transition_effect_v1
CREATE TRIGGER stewardship_runtime_transition_effect_v1 AFTER INSERT ON public.stewardship_runtime_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_runtime_transition_effect_v1();

-- TRIGGER: stewardship_runtime_transition stewardship_runtime_transition_immutable_guard_v1
CREATE TRIGGER stewardship_runtime_transition_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_runtime_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_runtime_transition_immutable_v1();

-- TRIGGER: stewardship_runtime_transition stewardship_runtime_transition_v1
CREATE TRIGGER stewardship_runtime_transition_v1 BEFORE INSERT ON public.stewardship_runtime_transition FOR EACH ROW EXECUTE FUNCTION public.stewardship_runtime_transition_v1();

-- TRIGGER: stewardship_schedule_definition stewardship_schedule_definition_mutable_guard_v1
CREATE TRIGGER stewardship_schedule_definition_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_schedule_definition FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_definition_mutable_v1();

-- TRIGGER: stewardship_schedule_definition stewardship_schedule_definition_v1
CREATE TRIGGER stewardship_schedule_definition_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_schedule_definition FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_definition_v1();

-- TRIGGER: stewardship_schedule_fulfillment stewardship_schedule_fulfillment_immutable_guard_v1
CREATE TRIGGER stewardship_schedule_fulfillment_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_schedule_fulfillment FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_fulfillment_immutable_v1();

-- TRIGGER: stewardship_schedule_occurrence stewardship_schedule_occurrence_mutable_guard_v1
CREATE TRIGGER stewardship_schedule_occurrence_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_schedule_occurrence FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_occurrence_mutable_v1();

-- TRIGGER: stewardship_schedule_revision stewardship_schedule_revision_immutable_guard_v1
CREATE TRIGGER stewardship_schedule_revision_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_schedule_revision FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_revision_immutable_v1();

-- TRIGGER: stewardship_schedule_definition stewardship_schedule_selection_effect_v1
CREATE TRIGGER stewardship_schedule_selection_effect_v1 AFTER INSERT OR UPDATE ON public.stewardship_schedule_definition FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_selection_effect_v1();

-- TRIGGER: stewardship_schedule_selection stewardship_schedule_selection_immutable_guard_v1
CREATE TRIGGER stewardship_schedule_selection_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_schedule_selection FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedule_selection_immutable_v1();

-- TRIGGER: stewardship_sealed_credential_staging stewardship_sealed_intake_admission_v1
CREATE TRIGGER stewardship_sealed_intake_admission_v1 BEFORE INSERT ON public.stewardship_sealed_credential_staging FOR EACH ROW EXECUTE FUNCTION public.stewardship_sealed_intake_admission_v1();

-- TRIGGER: stewardship_secret_request stewardship_sealed_intake_admission_v1
CREATE TRIGGER stewardship_sealed_intake_admission_v1 BEFORE INSERT ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_sealed_intake_admission_v1();

-- TRIGGER: stewardship_sealed_credential_staging stewardship_sealed_staging_guard_v1
CREATE TRIGGER stewardship_sealed_staging_guard_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_sealed_credential_staging FOR EACH ROW EXECUTE FUNCTION public.stewardship_sealed_staging_guard_v1();

-- TRIGGER: stewardship_secret_checkpoint stewardship_secret_checkpoint_immutable_guard_v1
CREATE TRIGGER stewardship_secret_checkpoint_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_secret_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_secret_checkpoint_immutable_v1();

-- TRIGGER: stewardship_secret_checkpoint stewardship_secret_checkpoint_v1
CREATE TRIGGER stewardship_secret_checkpoint_v1 BEFORE INSERT ON public.stewardship_secret_checkpoint FOR EACH ROW EXECUTE FUNCTION public.stewardship_secret_checkpoint_v1();

-- TRIGGER: stewardship_secret_request stewardship_secret_history_v1
CREATE TRIGGER stewardship_secret_history_v1 AFTER INSERT OR UPDATE ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_secret_history_v1();

-- TRIGGER: stewardship_secret_request stewardship_secret_request_mutable_guard_v1
CREATE TRIGGER stewardship_secret_request_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_secret_request_mutable_v1();

-- TRIGGER: stewardship_secret_request stewardship_secret_state_v1
CREATE TRIGGER stewardship_secret_state_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_secret_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_secret_state_v2();

-- TRIGGER: stewardship_config_activation stewardship_setup_activation_completion
CREATE CONSTRAINT TRIGGER stewardship_setup_activation_completion AFTER INSERT ON public.stewardship_config_activation DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_required_v1();

-- TRIGGER: stewardship_setup_completion stewardship_setup_atomic_completion
CREATE CONSTRAINT TRIGGER stewardship_setup_atomic_completion AFTER INSERT ON public.stewardship_setup_completion DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_required_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_attempt_admission
CREATE TRIGGER stewardship_setup_attempt_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_attempt_guard_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_attempt_audit
CREATE TRIGGER stewardship_setup_attempt_audit AFTER INSERT OR UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_attempt_audit_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_attempt_mutable_guard_v1
CREATE TRIGGER stewardship_setup_attempt_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_attempt_mutable_v1();

-- TRIGGER: stewardship_setup_completion stewardship_setup_completion_immutable_guard_v1
CREATE TRIGGER stewardship_setup_completion_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_completion FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_immutable_v1();

-- TRIGGER: stewardship_setup_completion stewardship_setup_completion_insert
CREATE TRIGGER stewardship_setup_completion_insert BEFORE INSERT ON public.stewardship_setup_completion FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_insert_v1();

-- TRIGGER: stewardship_setup_config_abort stewardship_setup_config_abort_admission
CREATE TRIGGER stewardship_setup_config_abort_admission BEFORE INSERT ON public.stewardship_setup_config_abort FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_config_abort_v1();

-- TRIGGER: stewardship_setup_config_abort stewardship_setup_config_abort_immutable_guard_v1
CREATE TRIGGER stewardship_setup_config_abort_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_config_abort FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_config_abort_immutable_v1();

-- TRIGGER: stewardship_setup_config_intent stewardship_setup_config_intent_admission
CREATE TRIGGER stewardship_setup_config_intent_admission BEFORE INSERT ON public.stewardship_setup_config_intent FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_config_intent_v1();

-- TRIGGER: stewardship_setup_config_intent stewardship_setup_config_intent_immutable_guard_v1
CREATE TRIGGER stewardship_setup_config_intent_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_config_intent FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_config_intent_immutable_v1();

-- TRIGGER: stewardship_setup_credential_install stewardship_setup_credential_install_immutable_guard_v1
CREATE TRIGGER stewardship_setup_credential_install_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_credential_install FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_credential_install_immutable_v1();

-- TRIGGER: stewardship_setup_draft_section stewardship_setup_draft_admission
CREATE TRIGGER stewardship_setup_draft_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_draft_section FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_draft_guard_v1();

-- TRIGGER: stewardship_setup_draft_section stewardship_setup_draft_section_mutable_guard_v1
CREATE TRIGGER stewardship_setup_draft_section_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_draft_section FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_draft_section_mutable_v1();

-- TRIGGER: stewardship_setup_source_exchange stewardship_setup_exchange_admission
CREATE TRIGGER stewardship_setup_exchange_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_source_exchange FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_exchange_guard_v1();

-- TRIGGER: stewardship_setup_credential_install stewardship_setup_install_binding
CREATE TRIGGER stewardship_setup_install_binding BEFORE INSERT ON public.stewardship_setup_credential_install FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_install_binding_v1();

-- TRIGGER: stewardship_secret_request stewardship_setup_install_intake
CREATE CONSTRAINT TRIGGER stewardship_setup_install_intake AFTER INSERT ON public.stewardship_secret_request DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_install_intake_v1();

-- TRIGGER: stewardship_setup_mail_delivery stewardship_setup_mail_admission
CREATE TRIGGER stewardship_setup_mail_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_mail_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_guard_v1();

-- TRIGGER: stewardship_setup_mail_delivery stewardship_setup_mail_audit
CREATE TRIGGER stewardship_setup_mail_audit AFTER INSERT OR UPDATE ON public.stewardship_setup_mail_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_audit_v1();

-- TRIGGER: stewardship_setup_mail_delivery stewardship_setup_mail_delivery_mutable_guard_v1
CREATE TRIGGER stewardship_setup_mail_delivery_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_mail_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_delivery_mutable_v1();

-- TRIGGER: stewardship_setup_mail_exchange stewardship_setup_mail_exchange_admission
CREATE TRIGGER stewardship_setup_mail_exchange_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_mail_exchange FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_exchange_guard_v1();

-- TRIGGER: stewardship_setup_mail_exchange stewardship_setup_mail_exchange_mutable_guard_v1
CREATE TRIGGER stewardship_setup_mail_exchange_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_mail_exchange FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_mail_exchange_mutable_v1();

-- TRIGGER: stewardship_setup_prepared stewardship_setup_prepared_admission
CREATE TRIGGER stewardship_setup_prepared_admission BEFORE INSERT ON public.stewardship_setup_prepared FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_prepared_guard_v1();

-- TRIGGER: stewardship_setup_prepared stewardship_setup_prepared_immutable_guard_v1
CREATE TRIGGER stewardship_setup_prepared_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_prepared FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_prepared_immutable_v1();

-- TRIGGER: stewardship_setup_readiness_binding stewardship_setup_readiness_binding_immutable_guard_v1
CREATE TRIGGER stewardship_setup_readiness_binding_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_readiness_binding FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_readiness_binding_immutable_v1();

-- TRIGGER: stewardship_setup_readiness_binding stewardship_setup_readiness_insert
CREATE TRIGGER stewardship_setup_readiness_insert BEFORE INSERT ON public.stewardship_setup_readiness_binding FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_readiness_guard_v1();

-- TRIGGER: stewardship_config_request stewardship_setup_request_binding
CREATE CONSTRAINT TRIGGER stewardship_setup_request_binding AFTER INSERT ON public.stewardship_config_request DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_request_binding_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_scrub_drafts
CREATE TRIGGER stewardship_setup_scrub_drafts AFTER UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_drafts_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_scrub_exchanges
CREATE TRIGGER stewardship_setup_scrub_exchanges AFTER UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_exchanges_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_scrub_mail
CREATE TRIGGER stewardship_setup_scrub_mail AFTER UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_mail_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_scrub_mail_exchanges
CREATE TRIGGER stewardship_setup_scrub_mail_exchanges AFTER UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_mail_exchanges_v1();

-- TRIGGER: stewardship_setup_attempt stewardship_setup_scrub_secrets
CREATE TRIGGER stewardship_setup_scrub_secrets AFTER UPDATE ON public.stewardship_setup_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_scrub_secrets_v1();

-- TRIGGER: stewardship_setup_sealed_credential stewardship_setup_sealed_credential_mutable_guard_v1
CREATE TRIGGER stewardship_setup_sealed_credential_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_sealed_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_sealed_credential_mutable_v1();

-- TRIGGER: stewardship_setup_sealed_credential stewardship_setup_secret_admission
CREATE TRIGGER stewardship_setup_secret_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_sealed_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_secret_guard_v1();

-- TRIGGER: stewardship_setup_sealed_credential stewardship_setup_secret_audit
CREATE TRIGGER stewardship_setup_secret_audit AFTER INSERT OR UPDATE ON public.stewardship_setup_sealed_credential FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_secret_audit_v1();

-- TRIGGER: stewardship_setup_slack_delivery stewardship_setup_slack_admission
CREATE TRIGGER stewardship_setup_slack_admission BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_setup_slack_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_slack_guard_v1();

-- TRIGGER: stewardship_setup_slack_delivery stewardship_setup_slack_audit
CREATE TRIGGER stewardship_setup_slack_audit AFTER INSERT OR UPDATE ON public.stewardship_setup_slack_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_slack_audit_v1();

-- TRIGGER: stewardship_setup_slack_delivery stewardship_setup_slack_delivery_mutable_guard_v1
CREATE TRIGGER stewardship_setup_slack_delivery_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_slack_delivery FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_slack_delivery_mutable_v1();

-- TRIGGER: stewardship_source_current stewardship_setup_source_completion
CREATE CONSTRAINT TRIGGER stewardship_setup_source_completion AFTER UPDATE ON public.stewardship_source_current DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_completion_required_v1();

-- TRIGGER: stewardship_setup_source_exchange stewardship_setup_source_exchange_mutable_guard_v1
CREATE TRIGGER stewardship_setup_source_exchange_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_setup_source_exchange FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_source_exchange_mutable_v1();

-- TRIGGER: stewardship_setup_source_result stewardship_setup_source_result_immutable_guard_v1
CREATE TRIGGER stewardship_setup_source_result_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_setup_source_result FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_source_result_immutable_v1();

-- TRIGGER: stewardship_setup_source_result stewardship_setup_source_result_insert
CREATE TRIGGER stewardship_setup_source_result_insert BEFORE INSERT ON public.stewardship_setup_source_result FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_source_result_guard_v1();

-- TRIGGER: stewardship_system_configuration stewardship_setup_testing_recipient
CREATE TRIGGER stewardship_setup_testing_recipient BEFORE UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_testing_recipient_v1();

-- TRIGGER: stewardship_setup_draft_section stewardship_setup_timezone_guard
CREATE TRIGGER stewardship_setup_timezone_guard BEFORE UPDATE ON public.stewardship_setup_draft_section FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_timezone_guard_v1();

-- TRIGGER: stewardship_source_current stewardship_source_chair_effects
CREATE CONSTRAINT TRIGGER stewardship_source_chair_effects AFTER UPDATE ON public.stewardship_source_current DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_chair_effects_v1();

-- TRIGGER: stewardship_source_compaction stewardship_source_compaction_immutable_guard_v1
CREATE TRIGGER stewardship_source_compaction_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_source_compaction FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_compaction_immutable_v1();

-- TRIGGER: stewardship_source_current stewardship_source_current_mutable_guard_v1
CREATE TRIGGER stewardship_source_current_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_source_current FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_current_mutable_v1();

-- TRIGGER: stewardship_source_lease stewardship_source_lease_mutable_guard_v1
CREATE TRIGGER stewardship_source_lease_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_source_lease FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_lease_mutable_v1();

-- TRIGGER: stewardship_source_lease stewardship_source_lease_owner
CREATE TRIGGER stewardship_source_lease_owner BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_source_lease FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_lease_owner_guard();

-- TRIGGER: stewardship_source_pin stewardship_source_pin_mutable_guard_v1
CREATE TRIGGER stewardship_source_pin_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_source_pin FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_pin_mutable_v1();

-- TRIGGER: stewardship_source_refresh_attempt stewardship_source_refresh_attempt_immutable_guard_v1
CREATE TRIGGER stewardship_source_refresh_attempt_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_source_refresh_attempt FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_refresh_attempt_immutable_v1();

-- TRIGGER: stewardship_source_refresh_command stewardship_source_refresh_command_immutable_guard_v1
CREATE TRIGGER stewardship_source_refresh_command_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_source_refresh_command FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_refresh_command_immutable_v1();

-- TRIGGER: stewardship_source_refresh_fallback stewardship_source_refresh_fallback_immutable_guard_v1
CREATE TRIGGER stewardship_source_refresh_fallback_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_source_refresh_fallback FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_refresh_fallback_immutable_v1();

-- TRIGGER: stewardship_source_refresh_request stewardship_source_refresh_request_immutable_guard_v1
CREATE TRIGGER stewardship_source_refresh_request_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_source_refresh_request FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_refresh_request_immutable_v1();

-- TRIGGER: stewardship_source_refresh_tick stewardship_source_refresh_tick_immutable_guard_v1
CREATE TRIGGER stewardship_source_refresh_tick_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_source_refresh_tick FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_refresh_tick_immutable_v1();

-- TRIGGER: stewardship_source_snapshot stewardship_source_snapshot_mutable_guard_v1
CREATE TRIGGER stewardship_source_snapshot_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_source_snapshot FOR EACH ROW EXECUTE FUNCTION public.stewardship_source_snapshot_mutable_v1();

-- TRIGGER: stewardship_system_configuration stewardship_system_configuration_mutable_guard_v1
CREATE TRIGGER stewardship_system_configuration_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_system_configuration_mutable_v1();

-- TRIGGER: stewardship_task_event stewardship_task_event_binding_v1
CREATE TRIGGER stewardship_task_event_binding_v1 BEFORE INSERT ON public.stewardship_task_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_event_binding_v1();

-- TRIGGER: stewardship_task_event stewardship_task_event_immutable_guard_v1
CREATE TRIGGER stewardship_task_event_immutable_guard_v1 BEFORE DELETE OR UPDATE ON public.stewardship_task_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_event_immutable_v1();

-- TRIGGER: stewardship_task_event stewardship_task_event_phase_v1
CREATE TRIGGER stewardship_task_event_phase_v1 BEFORE INSERT ON public.stewardship_task_event FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_event_phase_v1();

-- TRIGGER: stewardship_task_run stewardship_task_history_v1
CREATE TRIGGER stewardship_task_history_v1 AFTER INSERT OR UPDATE ON public.stewardship_task_run FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_history_v1();

-- TRIGGER: stewardship_task_run stewardship_task_phase_v1
CREATE TRIGGER stewardship_task_phase_v1 BEFORE INSERT OR UPDATE ON public.stewardship_task_run FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_phase_v1();

-- TRIGGER: stewardship_task_run stewardship_task_run_mutable_guard_v1
CREATE TRIGGER stewardship_task_run_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_task_run FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_run_mutable_v1();

-- TRIGGER: stewardship_task_run stewardship_task_run_state_v1
CREATE TRIGGER stewardship_task_run_state_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_task_run FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_state_v1();

-- TRIGGER: stewardship_task_run stewardship_task_scheduler_v1
CREATE TRIGGER stewardship_task_scheduler_v1 BEFORE UPDATE ON public.stewardship_task_run FOR EACH ROW EXECUTE FUNCTION public.stewardship_task_scheduler_v1();

-- TRIGGER: stewardship_campaign stewardship_token_activation_v1
CREATE TRIGGER stewardship_token_activation_v1 BEFORE UPDATE ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_token_activation_v1();

-- TRIGGER: stewardship_campaign stewardship_token_campaign_effects_v1
CREATE TRIGGER stewardship_token_campaign_effects_v1 AFTER UPDATE ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_token_campaign_effects_v1();

-- TRIGGER: stewardship_campaign stewardship_token_gate_release_v1
CREATE TRIGGER stewardship_token_gate_release_v1 AFTER UPDATE ON public.stewardship_campaign FOR EACH ROW EXECUTE FUNCTION public.stewardship_token_gate_release_v1();

-- TRIGGER: stewardship_family_token_generation stewardship_token_generation_state_v1
CREATE TRIGGER stewardship_token_generation_state_v1 BEFORE INSERT OR UPDATE ON public.stewardship_family_token_generation FOR EACH ROW EXECUTE FUNCTION public.stewardship_token_generation_state_v1();

-- TRIGGER: stewardship_family_token stewardship_token_issuance_v1
CREATE TRIGGER stewardship_token_issuance_v1 BEFORE INSERT ON public.stewardship_family_token FOR EACH ROW EXECUTE FUNCTION public.stewardship_token_issuance_v1();

-- TRIGGER: stewardship_family_token_generation stewardship_token_request_binding_v1
CREATE TRIGGER stewardship_token_request_binding_v1 BEFORE UPDATE ON public.stewardship_family_token_generation FOR EACH ROW EXECUTE FUNCTION public.stewardship_token_request_binding_v1();

-- TRIGGER: stewardship_campaign_work_gate stewardship_work_gate_v1
CREATE TRIGGER stewardship_work_gate_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_campaign_work_gate FOR EACH ROW EXECUTE FUNCTION public.stewardship_work_gate_v1();

-- TRIGGER: stewardship_system_configuration zzz_stewardship_schedules_activate_v1
CREATE TRIGGER zzz_stewardship_schedules_activate_v1 AFTER UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_schedules_activate_v1();

-- TRIGGER: stewardship_system_configuration zzzz_stewardship_campaign_end_effect_v1
CREATE TRIGGER zzzz_stewardship_campaign_end_effect_v1 AFTER UPDATE ON public.stewardship_system_configuration FOR EACH ROW EXECUTE FUNCTION public.stewardship_campaign_end_effect_v1();

-- FK CONSTRAINT: stewardship_activation_catchup stewardship_activati_activation_id_1aec7a2f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_activation_catchup
    ADD CONSTRAINT stewardship_activati_activation_id_1aec7a2f_fk_stewardsh FOREIGN KEY (activation_id) REFERENCES public.stewardship_campaign_transition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_activation_catchup stewardship_activati_campaign_id_ff26e1cc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_activation_catchup
    ADD CONSTRAINT stewardship_activati_campaign_id_ff26e1cc_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_activation_catchup stewardship_activati_configuration_id_9837489b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_activation_catchup
    ADD CONSTRAINT stewardship_activati_configuration_id_9837489b_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_activation_catchup stewardship_activati_task_root_id_35986c98_fk_stewardsh
ALTER TABLE ONLY public.stewardship_activation_catchup
    ADD CONSTRAINT stewardship_activati_task_root_id_35986c98_fk_stewardsh FOREIGN KEY (task_root_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_address_rule stewardship_address__configuration_id_c1b524bc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_address_rule
    ADD CONSTRAINT stewardship_address__configuration_id_c1b524bc_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_address_grant stewardship_address__rule_id_0dad4fcb_fk_stewardsh
ALTER TABLE ONLY public.stewardship_address_grant
    ADD CONSTRAINT stewardship_address__rule_id_0dad4fcb_fk_stewardsh FOREIGN KEY (rule_id) REFERENCES public.stewardship_address_rule(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_admin_revocation stewardship_admin_re_activation_id_c4effc0a_fk_stewardsh
ALTER TABLE ONLY public.stewardship_admin_revocation
    ADD CONSTRAINT stewardship_admin_re_activation_id_c4effc0a_fk_stewardsh FOREIGN KEY (activation_id) REFERENCES public.stewardship_config_activation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_applied_integration stewardship_applied__configuration_id_9029654f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_applied_integration
    ADD CONSTRAINT stewardship_applied__configuration_id_9029654f_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_audit_context stewardship_audit_co_event_id_522a67e9_fk_stewardsh
ALTER TABLE ONLY public.stewardship_audit_context
    ADD CONSTRAINT stewardship_audit_co_event_id_522a67e9_fk_stewardsh FOREIGN KEY (event_id) REFERENCES public.stewardship_audit_event(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_audit_event stewardship_audit_ev_parish_id_9fc8f11a_fk_stewardsh
ALTER TABLE ONLY public.stewardship_audit_event
    ADD CONSTRAINT stewardship_audit_ev_parish_id_9fc8f11a_fk_stewardsh FOREIGN KEY (parish_id) REFERENCES public.stewardship_parish(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_branding_bundle stewardship_branding_base_id_23ab9158_fk_stewardsh
ALTER TABLE ONLY public.stewardship_branding_bundle
    ADD CONSTRAINT stewardship_branding_base_id_23ab9158_fk_stewardsh FOREIGN KEY (base_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_branding_asset stewardship_branding_bundle_id_fb37a2e9_fk_stewardsh
ALTER TABLE ONLY public.stewardship_branding_asset
    ADD CONSTRAINT stewardship_branding_bundle_id_fb37a2e9_fk_stewardsh FOREIGN KEY (bundle_id) REFERENCES public.stewardship_branding_bundle(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign stewardship_campaign_active_configuration_6a76077e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign
    ADD CONSTRAINT stewardship_campaign_active_configuration_6a76077e_fk_stewardsh FOREIGN KEY (active_configuration_id) REFERENCES public.stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_credentials stewardship_campaign_campaign_id_5f5d7f48_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_credentials
    ADD CONSTRAINT stewardship_campaign_campaign_id_5f5d7f48_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_boundary stewardship_campaign_campaign_id_66b48563_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_boundary
    ADD CONSTRAINT stewardship_campaign_campaign_id_66b48563_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_work_gate stewardship_campaign_campaign_id_7ecf7387_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_work_gate
    ADD CONSTRAINT stewardship_campaign_campaign_id_7ecf7387_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_control stewardship_campaign_campaign_id_ab98ed7a_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_control
    ADD CONSTRAINT stewardship_campaign_campaign_id_ab98ed7a_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_transition stewardship_campaign_campaign_id_cfaa465b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_transition
    ADD CONSTRAINT stewardship_campaign_campaign_id_cfaa465b_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_campaign_id_eb2e98ab_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_campaign_id_eb2e98ab_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_config_intent stewardship_campaign_campaign_id_ed1a1432_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_config_intent
    ADD CONSTRAINT stewardship_campaign_campaign_id_ed1a1432_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_configuration stewardship_campaign_configuration_id_84fe667f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_configuration
    ADD CONSTRAINT stewardship_campaign_configuration_id_84fe667f_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_configuration_id_dc633885_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_configuration_id_dc633885_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_transition stewardship_campaign_configuration_id_e65133d6_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_transition
    ADD CONSTRAINT stewardship_campaign_configuration_id_e65133d6_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_config_abort stewardship_campaign_intent_id_7c114085_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_config_abort
    ADD CONSTRAINT stewardship_campaign_intent_id_7c114085_fk_stewardsh FOREIGN KEY (intent_id) REFERENCES public.stewardship_campaign_config_intent(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_config_intent stewardship_campaign_prior_projection_id_1ef19299_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_config_intent
    ADD CONSTRAINT stewardship_campaign_prior_projection_id_1ef19299_fk_stewardsh FOREIGN KEY (prior_projection_id) REFERENCES public.stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_transition stewardship_campaign_prior_projection_id_444b9c8f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_transition
    ADD CONSTRAINT stewardship_campaign_prior_projection_id_444b9c8f_fk_stewardsh FOREIGN KEY (prior_projection_id) REFERENCES public.stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_credentials stewardship_campaign_rehearsal_epoch_id_9051a4f4_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_credentials
    ADD CONSTRAINT stewardship_campaign_rehearsal_epoch_id_9051a4f4_fk_stewardsh FOREIGN KEY (rehearsal_epoch_id) REFERENCES public.stewardship_rehearsal_epoch(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_config_intent stewardship_campaign_request_id_e35244d3_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_config_intent
    ADD CONSTRAINT stewardship_campaign_request_id_e35244d3_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_config_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_run_id_84c77c43_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_run_id_84c77c43_fk_stewardsh FOREIGN KEY (run_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_task_id_1449f210_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_task_id_1449f210_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_boundary stewardship_campaign_task_id_fc115736_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_boundary
    ADD CONSTRAINT stewardship_campaign_task_id_fc115736_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_mail_test stewardship_campaign_template_id_2b3ea72e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_mail_test
    ADD CONSTRAINT stewardship_campaign_template_id_2b3ea72e_fk_stewardsh FOREIGN KEY (template_id) REFERENCES public.stewardship_content_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_campaign_boundary stewardship_campaign_transition_id_e5f718ca_fk_stewardsh
ALTER TABLE ONLY public.stewardship_campaign_boundary
    ADD CONSTRAINT stewardship_campaign_transition_id_e5f718ca_fk_stewardsh FOREIGN KEY (transition_id) REFERENCES public.stewardship_campaign_transition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_catchup_checkpoint stewardship_catchup__demand_id_4a41843c_fk_stewardsh
ALTER TABLE ONLY public.stewardship_catchup_checkpoint
    ADD CONSTRAINT stewardship_catchup__demand_id_4a41843c_fk_stewardsh FOREIGN KEY (demand_id) REFERENCES public.stewardship_activation_catchup(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_catchup_failure stewardship_catchup__demand_id_78213b0e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_catchup_failure
    ADD CONSTRAINT stewardship_catchup__demand_id_78213b0e_fk_stewardsh FOREIGN KEY (demand_id) REFERENCES public.stewardship_activation_catchup(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_catchup_failure stewardship_catchup__task_id_6333f455_fk_stewardsh
ALTER TABLE ONLY public.stewardship_catchup_failure
    ADD CONSTRAINT stewardship_catchup__task_id_6333f455_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_catchup_checkpoint stewardship_catchup__task_id_a50a3998_fk_stewardsh
ALTER TABLE ONLY public.stewardship_catchup_checkpoint
    ADD CONSTRAINT stewardship_catchup__task_id_a50a3998_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_reconciliation stewardship_chair_re_activation_id_6f7e6178_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT stewardship_chair_re_activation_id_6f7e6178_fk_stewardsh FOREIGN KEY (activation_id) REFERENCES public.stewardship_config_activation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_review stewardship_chair_re_closed_by_id_bd11ec76_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_review
    ADD CONSTRAINT stewardship_chair_re_closed_by_id_bd11ec76_fk_stewardsh FOREIGN KEY (closed_by_id) REFERENCES public.stewardship_chair_reconciliation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_reconciliation stewardship_chair_re_configuration_id_31cd83cf_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT stewardship_chair_re_configuration_id_31cd83cf_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_review stewardship_chair_re_latest_by_id_6fcae486_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_review
    ADD CONSTRAINT stewardship_chair_re_latest_by_id_6fcae486_fk_stewardsh FOREIGN KEY (latest_by_id) REFERENCES public.stewardship_chair_reconciliation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_review stewardship_chair_re_opened_by_id_8594b2ca_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_review
    ADD CONSTRAINT stewardship_chair_re_opened_by_id_8594b2ca_fk_stewardsh FOREIGN KEY (opened_by_id) REFERENCES public.stewardship_chair_reconciliation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_reconciliation stewardship_chair_re_snapshot_id_e1a8872e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT stewardship_chair_re_snapshot_id_e1a8872e_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_reconciliation stewardship_chair_re_source_owner_id_c935bf61_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_reconciliation
    ADD CONSTRAINT stewardship_chair_re_source_owner_id_c935bf61_fk_stewardsh FOREIGN KEY (source_owner_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_seed_evidence stewardship_chair_se_assignment_id_1b9eda88_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_seed_evidence
    ADD CONSTRAINT stewardship_chair_se_assignment_id_1b9eda88_fk_stewardsh FOREIGN KEY (assignment_id) REFERENCES public.stewardship_ministry_assignment(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_chair_seed_evidence stewardship_chair_se_snapshot_id_f6051619_fk_stewardsh
ALTER TABLE ONLY public.stewardship_chair_seed_evidence
    ADD CONSTRAINT stewardship_chair_se_snapshot_id_f6051619_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_config_activation stewardship_config_a_configuration_id_303724ab_fk_stewardsh
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_a_configuration_id_303724ab_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_config_activation stewardship_config_a_predecessor_id_797080e8_fk_stewardsh
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_a_predecessor_id_797080e8_fk_stewardsh FOREIGN KEY (predecessor_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_config_activation stewardship_config_a_request_id_d5a77b12_fk_stewardsh
ALTER TABLE ONLY public.stewardship_config_activation
    ADD CONSTRAINT stewardship_config_a_request_id_d5a77b12_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_config_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_config_checkpoint stewardship_config_c_request_id_43a03b17_fk_stewardsh
ALTER TABLE ONLY public.stewardship_config_checkpoint
    ADD CONSTRAINT stewardship_config_c_request_id_43a03b17_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_config_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_config_request stewardship_config_r_base_id_fe781cd2_fk_stewardsh
ALTER TABLE ONLY public.stewardship_config_request
    ADD CONSTRAINT stewardship_config_r_base_id_fe781cd2_fk_stewardsh FOREIGN KEY (base_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_configuration_version stewardship_configur_predecessor_id_ded3a24f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_configuration_version
    ADD CONSTRAINT stewardship_configur_predecessor_id_ded3a24f_fk_stewardsh FOREIGN KEY (predecessor_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_content_version stewardship_content__configuration_id_7bb46bdb_fk_stewardsh
ALTER TABLE ONLY public.stewardship_content_version
    ADD CONSTRAINT stewardship_content__configuration_id_7bb46bdb_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_credential_consumer_ack stewardship_credenti_request_id_815d7b63_fk_stewardsh
ALTER TABLE ONLY public.stewardship_credential_consumer_ack
    ADD CONSTRAINT stewardship_credenti_request_id_815d7b63_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_secret_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_daily_fact_set stewardship_daily_fa_campaign_id_de8bb40b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_daily_fact_set
    ADD CONSTRAINT stewardship_daily_fa_campaign_id_de8bb40b_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_daily_fact stewardship_daily_fa_fact_set_id_16f81142_fk_stewardsh
ALTER TABLE ONLY public.stewardship_daily_fact
    ADD CONSTRAINT stewardship_daily_fa_fact_set_id_16f81142_fk_stewardsh FOREIGN KEY (fact_set_id) REFERENCES public.stewardship_daily_fact_set(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_daily_fact_set stewardship_daily_fa_source_id_e4b5981e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_daily_fact_set
    ADD CONSTRAINT stewardship_daily_fa_source_id_e4b5981e_fk_stewardsh FOREIGN KEY (source_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_daily_fact_set stewardship_daily_fa_task_id_f748238f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_daily_fact_set
    ADD CONSTRAINT stewardship_daily_fa_task_id_f748238f_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_daily_fact_set stewardship_daily_fa_timezone_configurati_b7346e6e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_daily_fact_set
    ADD CONSTRAINT stewardship_daily_fa_timezone_configurati_b7346e6e_fk_stewardsh FOREIGN KEY (timezone_configuration_id) REFERENCES public.stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_domain_rule stewardship_domain_r_configuration_id_4231e000_fk_stewardsh
ALTER TABLE ONLY public.stewardship_domain_rule
    ADD CONSTRAINT stewardship_domain_r_configuration_id_4231e000_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_compaction stewardship_fact_com_task_id_aea57528_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_compaction
    ADD CONSTRAINT stewardship_fact_com_task_id_aea57528_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_demand stewardship_fact_dem_campaign_id_a0b2c939_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT stewardship_fact_dem_campaign_id_a0b2c939_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_demand stewardship_fact_dem_claimed_generation_i_9c50a239_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT stewardship_fact_dem_claimed_generation_i_9c50a239_fk_stewardsh FOREIGN KEY (claimed_generation_id) REFERENCES public.stewardship_daily_fact_set(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_demand stewardship_fact_dem_claimed_task_id_6613aeca_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT stewardship_fact_dem_claimed_task_id_6613aeca_fk_stewardsh FOREIGN KEY (claimed_task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_demand stewardship_fact_dem_requested_source_id_5589a5cb_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT stewardship_fact_dem_requested_source_id_5589a5cb_fk_stewardsh FOREIGN KEY (requested_source_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_demand stewardship_fact_dem_requested_timezone_c_fc08ec37_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_demand
    ADD CONSTRAINT stewardship_fact_dem_requested_timezone_c_fc08ec37_fk_stewardsh FOREIGN KEY (requested_timezone_configuration_id) REFERENCES public.stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_pin stewardship_fact_pin_fact_set_id_2ff4a4d8_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_pin
    ADD CONSTRAINT stewardship_fact_pin_fact_set_id_2ff4a4d8_fk_stewardsh FOREIGN KEY (fact_set_id) REFERENCES public.stewardship_daily_fact_set(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_pointer stewardship_fact_poi_campaign_id_5ecd1fb9_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_pointer
    ADD CONSTRAINT stewardship_fact_poi_campaign_id_5ecd1fb9_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_fact_pointer stewardship_fact_poi_fact_set_id_f3e5219f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_fact_pointer
    ADD CONSTRAINT stewardship_fact_poi_fact_set_id_f3e5219f_fk_stewardsh FOREIGN KEY (fact_set_id) REFERENCES public.stewardship_daily_fact_set(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_campaign stewardship_family_c_campaign_id_a836c089_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_campaign
    ADD CONSTRAINT stewardship_family_c_campaign_id_a836c089_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_code_mac stewardship_family_c_campaign_id_cb036b47_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_code_mac
    ADD CONSTRAINT stewardship_family_c_campaign_id_cb036b47_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_code_mac stewardship_family_c_family_id_bfdb0267_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_code_mac
    ADD CONSTRAINT stewardship_family_c_family_id_bfdb0267_fk_stewardsh FOREIGN KEY (family_id) REFERENCES public.stewardship_family_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_eligibility stewardship_family_e_family_id_97f5cc47_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_eligibility
    ADD CONSTRAINT stewardship_family_e_family_id_97f5cc47_fk_stewardsh FOREIGN KEY (family_id) REFERENCES public.stewardship_family_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_session stewardship_family_s_family_id_330cde00_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_session
    ADD CONSTRAINT stewardship_family_s_family_id_330cde00_fk_stewardsh FOREIGN KEY (family_id) REFERENCES public.stewardship_family_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_session stewardship_family_s_rehearsal_epoch_id_40b4c432_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_session
    ADD CONSTRAINT stewardship_family_s_rehearsal_epoch_id_40b4c432_fk_stewardsh FOREIGN KEY (rehearsal_epoch_id) REFERENCES public.stewardship_rehearsal_epoch(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_session stewardship_family_s_session_id_5ff9c26d_fk_django_se
ALTER TABLE ONLY public.stewardship_family_session
    ADD CONSTRAINT stewardship_family_s_session_id_5ff9c26d_fk_django_se FOREIGN KEY (session_id) REFERENCES public.django_session(session_key) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_token stewardship_family_t_campaign_id_347844ce_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_token
    ADD CONSTRAINT stewardship_family_t_campaign_id_347844ce_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_token_generation stewardship_family_t_campaign_id_4a0bf6dd_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_token_generation
    ADD CONSTRAINT stewardship_family_t_campaign_id_4a0bf6dd_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_token_generation stewardship_family_t_configuration_id_4258ab65_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_token_generation
    ADD CONSTRAINT stewardship_family_t_configuration_id_4258ab65_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_campaign_configuration(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_token_generation stewardship_family_t_configuration_reques_9874afb9_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_token_generation
    ADD CONSTRAINT stewardship_family_t_configuration_reques_9874afb9_fk_stewardsh FOREIGN KEY (configuration_request_id) REFERENCES public.stewardship_config_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_token stewardship_family_t_family_id_5b602375_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_token
    ADD CONSTRAINT stewardship_family_t_family_id_5b602375_fk_stewardsh FOREIGN KEY (family_id) REFERENCES public.stewardship_family_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_family_token stewardship_family_t_generation_id_6bfa7076_fk_stewardsh
ALTER TABLE ONLY public.stewardship_family_token
    ADD CONSTRAINT stewardship_family_t_generation_id_6bfa7076_fk_stewardsh FOREIGN KEY (generation_id) REFERENCES public.stewardship_family_token_generation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_ministry_assignment stewardship_ministry_configuration_id_5f298c17_fk_stewardsh
ALTER TABLE ONLY public.stewardship_ministry_assignment
    ADD CONSTRAINT stewardship_ministry_configuration_id_5f298c17_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_ministry_activity stewardship_ministry_configuration_id_68b9cd45_fk_stewardsh
ALTER TABLE ONLY public.stewardship_ministry_activity
    ADD CONSTRAINT stewardship_ministry_configuration_id_68b9cd45_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_occurrence_transition stewardship_occurren_occurrence_id_f136d622_fk_stewardsh
ALTER TABLE ONLY public.stewardship_occurrence_transition
    ADD CONSTRAINT stewardship_occurren_occurrence_id_f136d622_fk_stewardsh FOREIGN KEY (occurrence_id) REFERENCES public.stewardship_schedule_occurrence(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_parish stewardship_parish_configuration_id_be1d5d31_fk_stewardsh
ALTER TABLE ONLY public.stewardship_parish
    ADD CONSTRAINT stewardship_parish_configuration_id_be1d5d31_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_policy_epoch stewardship_policy_e_activation_id_792b6777_fk_stewardsh
ALTER TABLE ONLY public.stewardship_policy_epoch
    ADD CONSTRAINT stewardship_policy_e_activation_id_792b6777_fk_stewardsh FOREIGN KEY (activation_id) REFERENCES public.stewardship_config_activation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_policy_security_event stewardship_policy_s_activation_id_a6ee0ad0_fk_stewardsh
ALTER TABLE ONLY public.stewardship_policy_security_event
    ADD CONSTRAINT stewardship_policy_s_activation_id_a6ee0ad0_fk_stewardsh FOREIGN KEY (activation_id) REFERENCES public.stewardship_config_activation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_portal_session stewardship_portal_s_session_id_0eb11329_fk_django_se
ALTER TABLE ONLY public.stewardship_portal_session
    ADD CONSTRAINT stewardship_portal_s_session_id_0eb11329_fk_django_se FOREIGN KEY (session_id) REFERENCES public.django_session(session_key) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_postclose_resolution stewardship_postclos_campaign_id_8bfa8023_fk_stewardsh
ALTER TABLE ONLY public.stewardship_postclose_resolution
    ADD CONSTRAINT stewardship_postclos_campaign_id_8bfa8023_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_postclose_resolution stewardship_postclos_occurrence_id_850a55fd_fk_stewardsh
ALTER TABLE ONLY public.stewardship_postclose_resolution
    ADD CONSTRAINT stewardship_postclos_occurrence_id_850a55fd_fk_stewardsh FOREIGN KEY (occurrence_id) REFERENCES public.stewardship_schedule_occurrence(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_postclose_resolution stewardship_postclos_task_id_f4814b75_fk_stewardsh
ALTER TABLE ONLY public.stewardship_postclose_resolution
    ADD CONSTRAINT stewardship_postclos_task_id_f4814b75_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_provider_context stewardship_provider_request_id_80daeb7b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_provider_context
    ADD CONSTRAINT stewardship_provider_request_id_80daeb7b_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_secret_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_rehearsal_epoch stewardship_rehearsa_campaign_id_4b5e57ff_fk_stewardsh
ALTER TABLE ONLY public.stewardship_rehearsal_epoch
    ADD CONSTRAINT stewardship_rehearsa_campaign_id_4b5e57ff_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_rehearsal_reservation stewardship_rehearsa_campaign_id_e7ce330f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_rehearsal_reservation
    ADD CONSTRAINT stewardship_rehearsa_campaign_id_e7ce330f_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_rehearsal_code_mac stewardship_rehearsa_credential_id_e7b83882_fk_stewardsh
ALTER TABLE ONLY public.stewardship_rehearsal_code_mac
    ADD CONSTRAINT stewardship_rehearsa_credential_id_e7b83882_fk_stewardsh FOREIGN KEY (credential_id) REFERENCES public.stewardship_rehearsal_credential(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_rehearsal_code_mac stewardship_rehearsa_epoch_id_883778c1_fk_stewardsh
ALTER TABLE ONLY public.stewardship_rehearsal_code_mac
    ADD CONSTRAINT stewardship_rehearsa_epoch_id_883778c1_fk_stewardsh FOREIGN KEY (epoch_id) REFERENCES public.stewardship_rehearsal_epoch(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_rehearsal_credential stewardship_rehearsa_epoch_id_abc6bce4_fk_stewardsh
ALTER TABLE ONLY public.stewardship_rehearsal_credential
    ADD CONSTRAINT stewardship_rehearsa_epoch_id_abc6bce4_fk_stewardsh FOREIGN KEY (epoch_id) REFERENCES public.stewardship_rehearsal_epoch(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_rehearsal_credential stewardship_rehearsa_family_id_78761ef6_fk_stewardsh
ALTER TABLE ONLY public.stewardship_rehearsal_credential
    ADD CONSTRAINT stewardship_rehearsa_family_id_78761ef6_fk_stewardsh FOREIGN KEY (family_id) REFERENCES public.stewardship_family_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_restore_delivery_hold stewardship_restore__definition_id_b44fcfb3_fk_stewardsh
ALTER TABLE ONLY public.stewardship_restore_delivery_hold
    ADD CONSTRAINT stewardship_restore__definition_id_b44fcfb3_fk_stewardsh FOREIGN KEY (definition_id) REFERENCES public.stewardship_schedule_definition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_restore_hold_resolution stewardship_restore__hold_id_ac13f013_fk_stewardsh
ALTER TABLE ONLY public.stewardship_restore_hold_resolution
    ADD CONSTRAINT stewardship_restore__hold_id_ac13f013_fk_stewardsh FOREIGN KEY (hold_id) REFERENCES public.stewardship_restore_delivery_hold(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_restore_hold_resolution stewardship_restore__recovery_occurrence__529b01e5_fk_stewardsh
ALTER TABLE ONLY public.stewardship_restore_hold_resolution
    ADD CONSTRAINT stewardship_restore__recovery_occurrence__529b01e5_fk_stewardsh FOREIGN KEY (recovery_occurrence_id) REFERENCES public.stewardship_schedule_occurrence(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_restore_delivery_hold stewardship_restore__recovery_occurrence__db5d60b4_fk_stewardsh
ALTER TABLE ONLY public.stewardship_restore_delivery_hold
    ADD CONSTRAINT stewardship_restore__recovery_occurrence__db5d60b4_fk_stewardsh FOREIGN KEY (recovery_occurrence_id) REFERENCES public.stewardship_schedule_occurrence(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_runtime_transition stewardship_runtime__campaign_transition__cf55b633_fk_stewardsh
ALTER TABLE ONLY public.stewardship_runtime_transition
    ADD CONSTRAINT stewardship_runtime__campaign_transition__cf55b633_fk_stewardsh FOREIGN KEY (campaign_transition_id) REFERENCES public.stewardship_campaign_transition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_definition stewardship_schedule_campaign_id_3555aef5_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_definition
    ADD CONSTRAINT stewardship_schedule_campaign_id_3555aef5_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_selection stewardship_schedule_configuration_id_c690f572_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT stewardship_schedule_configuration_id_c690f572_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_revision stewardship_schedule_configuration_id_fbf8947f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_revision
    ADD CONSTRAINT stewardship_schedule_configuration_id_fbf8947f_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_definition stewardship_schedule_current_revision_id_fc0ba17f_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_definition
    ADD CONSTRAINT stewardship_schedule_current_revision_id_fc0ba17f_fk_stewardsh FOREIGN KEY (current_revision_id) REFERENCES public.stewardship_schedule_revision(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_selection stewardship_schedule_definition_id_24fa15bc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT stewardship_schedule_definition_id_24fa15bc_fk_stewardsh FOREIGN KEY (definition_id) REFERENCES public.stewardship_schedule_definition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_fulfillment stewardship_schedule_definition_id_6fdd18fb_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_fulfillment
    ADD CONSTRAINT stewardship_schedule_definition_id_6fdd18fb_fk_stewardsh FOREIGN KEY (definition_id) REFERENCES public.stewardship_schedule_definition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_occurrence stewardship_schedule_definition_id_af427c05_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT stewardship_schedule_definition_id_af427c05_fk_stewardsh FOREIGN KEY (definition_id) REFERENCES public.stewardship_schedule_definition(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_fulfillment stewardship_schedule_occurrence_id_049d97a8_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_fulfillment
    ADD CONSTRAINT stewardship_schedule_occurrence_id_049d97a8_fk_stewardsh FOREIGN KEY (occurrence_id) REFERENCES public.stewardship_schedule_occurrence(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_selection stewardship_schedule_previous_revision_id_fee2cd72_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT stewardship_schedule_previous_revision_id_fee2cd72_fk_stewardsh FOREIGN KEY (previous_revision_id) REFERENCES public.stewardship_schedule_revision(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_occurrence stewardship_schedule_replacement_id_de647db6_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT stewardship_schedule_replacement_id_de647db6_fk_stewardsh FOREIGN KEY (replacement_id) REFERENCES public.stewardship_schedule_occurrence(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_occurrence stewardship_schedule_revision_id_e39867be_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT stewardship_schedule_revision_id_e39867be_fk_stewardsh FOREIGN KEY (revision_id) REFERENCES public.stewardship_schedule_revision(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_selection stewardship_schedule_selected_revision_id_7f42ce52_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_selection
    ADD CONSTRAINT stewardship_schedule_selected_revision_id_7f42ce52_fk_stewardsh FOREIGN KEY (selected_revision_id) REFERENCES public.stewardship_schedule_revision(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_schedule_occurrence stewardship_schedule_task_id_87b5e54c_fk_stewardsh
ALTER TABLE ONLY public.stewardship_schedule_occurrence
    ADD CONSTRAINT stewardship_schedule_task_id_87b5e54c_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_sealed_credential_staging stewardship_sealed_c_request_id_193171dc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_sealed_credential_staging
    ADD CONSTRAINT stewardship_sealed_c_request_id_193171dc_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_secret_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_secret_checkpoint stewardship_secret_c_request_id_b256dff2_fk_stewardsh
ALTER TABLE ONLY public.stewardship_secret_checkpoint
    ADD CONSTRAINT stewardship_secret_c_request_id_b256dff2_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_secret_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_attempt stewardship_setup_at_base_id_91cd06e4_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_attempt
    ADD CONSTRAINT stewardship_setup_at_base_id_91cd06e4_fk_stewardsh FOREIGN KEY (base_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_attempt stewardship_setup_at_source_task_id_ed51da0e_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_attempt
    ADD CONSTRAINT stewardship_setup_at_source_task_id_ed51da0e_fk_stewardsh FOREIGN KEY (source_task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_completion stewardship_setup_co_activation_id_32f5382a_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_co_activation_id_32f5382a_fk_stewardsh FOREIGN KEY (activation_id) REFERENCES public.stewardship_config_activation(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_config_intent stewardship_setup_co_attempt_id_2c7339e9_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_config_intent
    ADD CONSTRAINT stewardship_setup_co_attempt_id_2c7339e9_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_setup_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_config_abort stewardship_setup_co_intent_id_bfb83476_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_config_abort
    ADD CONSTRAINT stewardship_setup_co_intent_id_bfb83476_fk_stewardsh FOREIGN KEY (intent_id) REFERENCES public.stewardship_setup_config_intent(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_completion stewardship_setup_co_preparation_id_e0bd3caa_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_co_preparation_id_e0bd3caa_fk_stewardsh FOREIGN KEY (preparation_id) REFERENCES public.stewardship_setup_prepared(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_config_intent stewardship_setup_co_request_id_b76fbc76_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_config_intent
    ADD CONSTRAINT stewardship_setup_co_request_id_b76fbc76_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_config_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_completion stewardship_setup_co_snapshot_id_7e50228a_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_co_snapshot_id_7e50228a_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_completion stewardship_setup_co_task_id_c4081a36_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_completion
    ADD CONSTRAINT stewardship_setup_co_task_id_c4081a36_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_credential_install stewardship_setup_cr_credential_id_50b03f1c_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT stewardship_setup_cr_credential_id_50b03f1c_fk_stewardsh FOREIGN KEY (credential_id) REFERENCES public.stewardship_setup_sealed_credential(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_credential_install stewardship_setup_cr_readiness_id_7969f012_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT stewardship_setup_cr_readiness_id_7969f012_fk_stewardsh FOREIGN KEY (readiness_id) REFERENCES public.stewardship_setup_readiness_binding(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_credential_install stewardship_setup_cr_request_id_bb477341_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_credential_install
    ADD CONSTRAINT stewardship_setup_cr_request_id_bb477341_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_secret_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_draft_section stewardship_setup_dr_attempt_id_58448a74_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_draft_section
    ADD CONSTRAINT stewardship_setup_dr_attempt_id_58448a74_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_setup_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_mail_delivery stewardship_setup_ma_attempt_id_befe550c_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT stewardship_setup_ma_attempt_id_befe550c_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_setup_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_mail_delivery stewardship_setup_ma_credential_id_24219ecf_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT stewardship_setup_ma_credential_id_24219ecf_fk_stewardsh FOREIGN KEY (credential_id) REFERENCES public.stewardship_setup_sealed_credential(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_mail_exchange stewardship_setup_ma_delivery_id_82256d92_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_mail_exchange
    ADD CONSTRAINT stewardship_setup_ma_delivery_id_82256d92_fk_stewardsh FOREIGN KEY (delivery_id) REFERENCES public.stewardship_setup_mail_delivery(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_mail_exchange stewardship_setup_ma_run_id_98d28b1b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_mail_exchange
    ADD CONSTRAINT stewardship_setup_ma_run_id_98d28b1b_fk_stewardsh FOREIGN KEY (run_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_mail_delivery stewardship_setup_ma_run_id_e8502211_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT stewardship_setup_ma_run_id_e8502211_fk_stewardsh FOREIGN KEY (run_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_mail_delivery stewardship_setup_ma_task_id_662e4e40_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_mail_delivery
    ADD CONSTRAINT stewardship_setup_ma_task_id_662e4e40_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_prepared stewardship_setup_pr_configuration_id_ba729028_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_prepared
    ADD CONSTRAINT stewardship_setup_pr_configuration_id_ba729028_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_prepared stewardship_setup_pr_readiness_id_28b1736c_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_prepared
    ADD CONSTRAINT stewardship_setup_pr_readiness_id_28b1736c_fk_stewardsh FOREIGN KEY (readiness_id) REFERENCES public.stewardship_setup_readiness_binding(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_readiness_binding stewardship_setup_re_intent_id_40cea897_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_readiness_binding
    ADD CONSTRAINT stewardship_setup_re_intent_id_40cea897_fk_stewardsh FOREIGN KEY (intent_id) REFERENCES public.stewardship_setup_config_intent(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_readiness_binding stewardship_setup_re_mail_delivery_id_d122b14b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_readiness_binding
    ADD CONSTRAINT stewardship_setup_re_mail_delivery_id_d122b14b_fk_stewardsh FOREIGN KEY (mail_delivery_id) REFERENCES public.stewardship_setup_mail_delivery(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_readiness_binding stewardship_setup_re_slack_delivery_id_29b81023_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_readiness_binding
    ADD CONSTRAINT stewardship_setup_re_slack_delivery_id_29b81023_fk_stewardsh FOREIGN KEY (slack_delivery_id) REFERENCES public.stewardship_setup_slack_delivery(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_readiness_binding stewardship_setup_re_source_result_id_e33ae430_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_readiness_binding
    ADD CONSTRAINT stewardship_setup_re_source_result_id_e33ae430_fk_stewardsh FOREIGN KEY (source_result_id) REFERENCES public.stewardship_setup_source_result(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_sealed_credential stewardship_setup_se_attempt_id_6b5927d4_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_sealed_credential
    ADD CONSTRAINT stewardship_setup_se_attempt_id_6b5927d4_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_setup_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_slack_delivery stewardship_setup_sl_attempt_id_100dafb1_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_slack_delivery
    ADD CONSTRAINT stewardship_setup_sl_attempt_id_100dafb1_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_setup_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_slack_delivery stewardship_setup_sl_credential_id_8b1f948b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_slack_delivery
    ADD CONSTRAINT stewardship_setup_sl_credential_id_8b1f948b_fk_stewardsh FOREIGN KEY (credential_id) REFERENCES public.stewardship_setup_sealed_credential(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_source_exchange stewardship_setup_so_attempt_id_2da70c13_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_source_exchange
    ADD CONSTRAINT stewardship_setup_so_attempt_id_2da70c13_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_setup_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_source_exchange stewardship_setup_so_credential_id_64e46e64_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_source_exchange
    ADD CONSTRAINT stewardship_setup_so_credential_id_64e46e64_fk_stewardsh FOREIGN KEY (credential_id) REFERENCES public.stewardship_setup_sealed_credential(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_source_result stewardship_setup_so_exchange_id_b1b3a010_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_source_result
    ADD CONSTRAINT stewardship_setup_so_exchange_id_b1b3a010_fk_stewardsh FOREIGN KEY (exchange_id) REFERENCES public.stewardship_setup_source_exchange(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_source_result stewardship_setup_so_snapshot_id_74158055_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_source_result
    ADD CONSTRAINT stewardship_setup_so_snapshot_id_74158055_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_setup_source_exchange stewardship_setup_so_task_id_d4db3fa7_fk_stewardsh
ALTER TABLE ONLY public.stewardship_setup_source_exchange
    ADD CONSTRAINT stewardship_setup_so_task_id_d4db3fa7_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_contact stewardship_snapshot_payload_id_2289dcae_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_contact
    ADD CONSTRAINT stewardship_snapshot_payload_id_2289dcae_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_contact(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_roster stewardship_snapshot_payload_id_5d8be951_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_roster
    ADD CONSTRAINT stewardship_snapshot_payload_id_5d8be951_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_roster(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_contribution stewardship_snapshot_payload_id_6111da39_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_contribution
    ADD CONSTRAINT stewardship_snapshot_payload_id_6111da39_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_contribution(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_address stewardship_snapshot_payload_id_64eb66d2_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_address
    ADD CONSTRAINT stewardship_snapshot_payload_id_64eb66d2_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_address(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_member stewardship_snapshot_payload_id_7848b5dc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_member
    ADD CONSTRAINT stewardship_snapshot_payload_id_7848b5dc_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_member(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_fund stewardship_snapshot_payload_id_8da27a26_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_fund
    ADD CONSTRAINT stewardship_snapshot_payload_id_8da27a26_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_fund(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_pledge stewardship_snapshot_payload_id_9f3fc629_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_pledge
    ADD CONSTRAINT stewardship_snapshot_payload_id_9f3fc629_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_pledge(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_ministry stewardship_snapshot_payload_id_d0ff7fbb_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_ministry
    ADD CONSTRAINT stewardship_snapshot_payload_id_d0ff7fbb_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_ministry(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_family stewardship_snapshot_payload_id_f9e2ecf6_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_family
    ADD CONSTRAINT stewardship_snapshot_payload_id_f9e2ecf6_fk_stewardsh FOREIGN KEY (payload_id) REFERENCES public.stewardship_source_family(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_member stewardship_snapshot_snapshot_id_068e177c_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_member
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_068e177c_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_family stewardship_snapshot_snapshot_id_823c7910_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_family
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_823c7910_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_address stewardship_snapshot_snapshot_id_8543fae0_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_address
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_8543fae0_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_roster stewardship_snapshot_snapshot_id_a0649562_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_roster
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_a0649562_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_fund stewardship_snapshot_snapshot_id_a4855404_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_fund
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_a4855404_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_contribution stewardship_snapshot_snapshot_id_b12a39bf_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_contribution
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_b12a39bf_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_ministry stewardship_snapshot_snapshot_id_b578d514_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_ministry
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_b578d514_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_pledge stewardship_snapshot_snapshot_id_e8d49637_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_pledge
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_e8d49637_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_snapshot_contact stewardship_snapshot_snapshot_id_edecb2e6_fk_stewardsh
ALTER TABLE ONLY public.stewardship_snapshot_contact
    ADD CONSTRAINT stewardship_snapshot_snapshot_id_edecb2e6_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_current stewardship_source_c_snapshot_id_4e2edac1_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_current
    ADD CONSTRAINT stewardship_source_c_snapshot_id_4e2edac1_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_compaction stewardship_source_c_task_id_9d0dbab8_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_compaction
    ADD CONSTRAINT stewardship_source_c_task_id_9d0dbab8_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_lease stewardship_source_l_owner_id_d199382b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_lease
    ADD CONSTRAINT stewardship_source_l_owner_id_d199382b_fk_stewardsh FOREIGN KEY (owner_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_pin stewardship_source_p_snapshot_id_c637cd81_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_pin
    ADD CONSTRAINT stewardship_source_p_snapshot_id_c637cd81_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_r_attempt_id_ba35c2ee_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_r_attempt_id_ba35c2ee_fk_stewardsh FOREIGN KEY (attempt_id) REFERENCES public.stewardship_source_refresh_attempt(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_request stewardship_source_r_campaign_id_44d23ffc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_request
    ADD CONSTRAINT stewardship_source_r_campaign_id_44d23ffc_fk_stewardsh FOREIGN KEY (campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_tick stewardship_source_r_command_id_abb1eb46_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_tick
    ADD CONSTRAINT stewardship_source_r_command_id_abb1eb46_fk_stewardsh FOREIGN KEY (command_id) REFERENCES public.stewardship_source_refresh_command(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_r_command_id_b47959cb_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_r_command_id_b47959cb_fk_stewardsh FOREIGN KEY (command_id) REFERENCES public.stewardship_source_refresh_command(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_tick stewardship_source_r_configuration_id_0227e5f3_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_tick
    ADD CONSTRAINT stewardship_source_r_configuration_id_0227e5f3_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_attempt stewardship_source_r_configuration_id_0e9ed759_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT stewardship_source_r_configuration_id_0e9ed759_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_request stewardship_source_r_configuration_id_3e685a4b_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_request
    ADD CONSTRAINT stewardship_source_r_configuration_id_3e685a4b_fk_stewardsh FOREIGN KEY (configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_command stewardship_source_r_request_id_48122291_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_command
    ADD CONSTRAINT stewardship_source_r_request_id_48122291_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_source_refresh_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_r_request_id_7e1b48c4_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_r_request_id_7e1b48c4_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_source_refresh_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_attempt stewardship_source_r_request_id_b2100202_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT stewardship_source_r_request_id_b2100202_fk_stewardsh FOREIGN KEY (request_id) REFERENCES public.stewardship_source_refresh_request(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_attempt stewardship_source_r_snapshot_id_69b1d054_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT stewardship_source_r_snapshot_id_69b1d054_fk_stewardsh FOREIGN KEY (snapshot_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_fallback stewardship_source_r_task_id_c6dcabe9_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_fallback
    ADD CONSTRAINT stewardship_source_r_task_id_c6dcabe9_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_attempt stewardship_source_r_task_id_ef7e4924_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_attempt
    ADD CONSTRAINT stewardship_source_r_task_id_ef7e4924_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_refresh_request stewardship_source_r_task_root_id_33cd424d_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_refresh_request
    ADD CONSTRAINT stewardship_source_r_task_root_id_33cd424d_fk_stewardsh FOREIGN KEY (task_root_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_snapshot stewardship_source_s_base_id_5be645dc_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_snapshot
    ADD CONSTRAINT stewardship_source_s_base_id_5be645dc_fk_stewardsh FOREIGN KEY (base_id) REFERENCES public.stewardship_source_snapshot(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_source_snapshot stewardship_source_s_task_id_81d89e02_fk_stewardsh
ALTER TABLE ONLY public.stewardship_source_snapshot
    ADD CONSTRAINT stewardship_source_s_task_id_81d89e02_fk_stewardsh FOREIGN KEY (task_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_system_configuration stewardship_system_c_active_configuration_678237b7_fk_stewardsh
ALTER TABLE ONLY public.stewardship_system_configuration
    ADD CONSTRAINT stewardship_system_c_active_configuration_678237b7_fk_stewardsh FOREIGN KEY (active_configuration_id) REFERENCES public.stewardship_configuration_version(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_system_configuration stewardship_system_c_current_campaign_id_0e964cca_fk_stewardsh
ALTER TABLE ONLY public.stewardship_system_configuration
    ADD CONSTRAINT stewardship_system_c_current_campaign_id_0e964cca_fk_stewardsh FOREIGN KEY (current_campaign_id) REFERENCES public.stewardship_campaign(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_task_event stewardship_task_eve_run_id_36a778b8_fk_stewardsh
ALTER TABLE ONLY public.stewardship_task_event
    ADD CONSTRAINT stewardship_task_eve_run_id_36a778b8_fk_stewardsh FOREIGN KEY (run_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_task_run stewardship_task_run_parent_id_0a8fdab8_fk_stewardsh
ALTER TABLE ONLY public.stewardship_task_run
    ADD CONSTRAINT stewardship_task_run_parent_id_0a8fdab8_fk_stewardsh FOREIGN KEY (parent_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- FK CONSTRAINT: stewardship_task_run stewardship_task_run_root_id_f9d3f773_fk_stewardsh
ALTER TABLE ONLY public.stewardship_task_run
    ADD CONSTRAINT stewardship_task_run_root_id_f9d3f773_fk_stewardsh FOREIGN KEY (root_id) REFERENCES public.stewardship_task_run(id) DEFERRABLE INITIALLY DEFERRED;

-- POLICY: stewardship_secret_request setup_final_receipt_read
CREATE POLICY setup_final_receipt_read ON public.stewardship_secret_request FOR SELECT USING (public.stewardship_setup_final_receipt_read_v1(id));

-- POLICY: stewardship_provider_context setup_initial_context_intake
CREATE POLICY setup_initial_context_intake ON public.stewardship_provider_context FOR INSERT WITH CHECK (((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)) AND (EXISTS ( SELECT 1
   FROM public.stewardship_setup_credential_install binding
  WHERE (binding.request_id = stewardship_provider_context.request_id)))));

-- POLICY: stewardship_sealed_credential_staging setup_initial_staging_intake
CREATE POLICY setup_initial_staging_intake ON public.stewardship_sealed_credential_staging FOR INSERT WITH CHECK (((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)) AND (EXISTS ( SELECT 1
   FROM public.stewardship_setup_credential_install binding
  WHERE (binding.request_id = stewardship_sealed_credential_staging.request_id)))));

-- POLICY: stewardship_setup_draft_section setup_public_scope
CREATE POLICY setup_public_scope ON public.stewardship_setup_draft_section USING (((CURRENT_USER <> 'pk_stewardship_credential_slack'::name) OR ((step)::text = 'slack'::text))) WITH CHECK ((CURRENT_USER <> 'pk_stewardship_credential_slack'::name));

-- POLICY: stewardship_setup_sealed_credential setup_secret_change
CREATE POLICY setup_secret_change ON public.stewardship_setup_sealed_credential FOR UPDATE USING ((CURRENT_USER = ANY (ARRAY['pk_stewardship_web'::name, 'pk_stewardship_scheduler'::name]))) WITH CHECK ((CURRENT_USER = ANY (ARRAY['pk_stewardship_web'::name, 'pk_stewardship_scheduler'::name])));

-- POLICY: stewardship_setup_sealed_credential setup_secret_completed_metadata
CREATE POLICY setup_secret_completed_metadata ON public.stewardship_setup_sealed_credential FOR SELECT USING (public.stewardship_setup_completion_scrub_v1(attempt_id));

-- POLICY: stewardship_setup_sealed_credential setup_secret_completed_scrub
CREATE POLICY setup_secret_completed_scrub ON public.stewardship_setup_sealed_credential FOR UPDATE USING (public.stewardship_setup_completion_scrub_v1(attempt_id)) WITH CHECK (public.stewardship_setup_completion_scrub_v1(attempt_id));

-- POLICY: stewardship_setup_sealed_credential setup_secret_intake
CREATE POLICY setup_secret_intake ON public.stewardship_setup_sealed_credential FOR INSERT WITH CHECK ((CURRENT_USER = 'pk_stewardship_web'::name));

-- POLICY: stewardship_setup_sealed_credential setup_secret_mail_metadata
CREATE POLICY setup_secret_mail_metadata ON public.stewardship_setup_sealed_credential FOR SELECT USING (((CURRENT_USER = 'pk_stewardship_mail_dispatch'::name) AND ((target)::text = 'google_workspace'::text)));

-- POLICY: stewardship_setup_sealed_credential setup_secret_read
CREATE POLICY setup_secret_read ON public.stewardship_setup_sealed_credential FOR SELECT USING (((CURRENT_USER = ANY (ARRAY['pk_stewardship_web'::name, 'pk_stewardship_scheduler'::name, 'pk_stewardship_migration'::name])) OR (CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text))));

-- POLICY: stewardship_setup_sealed_credential setup_secret_source_metadata
CREATE POLICY setup_secret_source_metadata ON public.stewardship_setup_sealed_credential FOR SELECT USING (((CURRENT_USER = 'pk_stewardship_worker'::name) AND ((target)::text = 'parishsoft'::text)));

-- POLICY: stewardship_credential_consumer_ack stewardship_ack_read
CREATE POLICY stewardship_ack_read ON public.stewardship_credential_consumer_ack FOR SELECT USING ((EXISTS ( SELECT 1
   FROM public.stewardship_secret_request r
  WHERE (r.id = stewardship_credential_consumer_ack.request_id))));

-- POLICY: stewardship_credential_consumer_ack stewardship_ack_write
CREATE POLICY stewardship_ack_write ON public.stewardship_credential_consumer_ack FOR INSERT WITH CHECK ((CURRENT_USER = ('pk_stewardship_'::text || replace((consumer)::text, '-'::text, '_'::text))));

-- POLICY: stewardship_provider_context stewardship_config_provider_read
CREATE POLICY stewardship_config_provider_read ON public.stewardship_provider_context FOR SELECT USING (((CURRENT_USER = 'pk_stewardship_config_installer'::name) AND ((target)::text = ANY ((ARRAY['parishsoft'::character varying, 'google_workspace'::character varying, 'slack'::character varying])::text[]))));

-- POLICY: stewardship_secret_request stewardship_config_secret_read
CREATE POLICY stewardship_config_secret_read ON public.stewardship_secret_request FOR SELECT USING (((CURRENT_USER = 'pk_stewardship_config_installer'::name) AND ((target)::text = ANY ((ARRAY['parishsoft'::character varying, 'google_workspace'::character varying, 'slack'::character varying])::text[]))));

-- ROW SECURITY: stewardship_credential_consumer_ack
ALTER TABLE public.stewardship_credential_consumer_ack ENABLE ROW LEVEL SECURITY;

-- POLICY: stewardship_credential_consumer_ack stewardship_migration_initial_read
CREATE POLICY stewardship_migration_initial_read ON public.stewardship_credential_consumer_ack FOR SELECT USING ((CURRENT_USER = 'pk_stewardship_migration'::name));

-- POLICY: stewardship_sealed_credential_staging stewardship_migration_initial_read
CREATE POLICY stewardship_migration_initial_read ON public.stewardship_sealed_credential_staging FOR SELECT USING ((CURRENT_USER = 'pk_stewardship_migration'::name));

-- POLICY: stewardship_secret_request stewardship_migration_initial_read
CREATE POLICY stewardship_migration_initial_read ON public.stewardship_secret_request FOR SELECT USING ((CURRENT_USER = 'pk_stewardship_migration'::name));

-- ROW SECURITY: stewardship_provider_context
ALTER TABLE public.stewardship_provider_context ENABLE ROW LEVEL SECURITY;

-- POLICY: stewardship_provider_context stewardship_provider_context_intake
CREATE POLICY stewardship_provider_context_intake ON public.stewardship_provider_context FOR INSERT WITH CHECK ((CURRENT_USER = 'pk_stewardship_web'::name));

-- POLICY: stewardship_provider_context stewardship_provider_context_read
CREATE POLICY stewardship_provider_context_read ON public.stewardship_provider_context FOR SELECT USING (((CURRENT_USER = 'pk_stewardship_web'::name) OR (CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)) OR (CURRENT_USER = 'pk_stewardship_migration'::name)));

-- ROW SECURITY: stewardship_public_credential_handoff
ALTER TABLE public.stewardship_public_credential_handoff ENABLE ROW LEVEL SECURITY;

-- POLICY: stewardship_public_credential_handoff stewardship_public_handoff_initial_read
CREATE POLICY stewardship_public_handoff_initial_read ON public.stewardship_public_credential_handoff FOR SELECT USING ((CURRENT_USER = 'pk_stewardship_migration'::name));

-- POLICY: stewardship_public_credential_handoff stewardship_public_handoff_publish
CREATE POLICY stewardship_public_handoff_publish ON public.stewardship_public_credential_handoff FOR INSERT WITH CHECK ((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)));

-- POLICY: stewardship_public_credential_handoff stewardship_public_handoff_read
CREATE POLICY stewardship_public_handoff_read ON public.stewardship_public_credential_handoff FOR SELECT USING (((CURRENT_USER = 'pk_stewardship_web'::name) OR (CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text))));

-- ROW SECURITY: stewardship_sealed_credential_staging
ALTER TABLE public.stewardship_sealed_credential_staging ENABLE ROW LEVEL SECURITY;

-- ROW SECURITY: stewardship_secret_request
ALTER TABLE public.stewardship_secret_request ENABLE ROW LEVEL SECURITY;

-- POLICY: stewardship_secret_request stewardship_secret_target_scope
CREATE POLICY stewardship_secret_target_scope ON public.stewardship_secret_request USING (((CURRENT_USER = ANY (ARRAY['pk_stewardship_web'::name, 'pk_stewardship_backup_worker'::name])) OR (CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)) OR (EXISTS ( SELECT 1
   FROM jsonb_array_elements_text(stewardship_secret_request.required_consumers) c(value)
  WHERE (CURRENT_USER = ('pk_stewardship_'::text || replace(c.value, '-'::text, '_'::text))))))) WITH CHECK (((CURRENT_USER = 'pk_stewardship_web'::name) OR (CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text))));

-- ROW SECURITY: stewardship_setup_draft_section
ALTER TABLE public.stewardship_setup_draft_section ENABLE ROW LEVEL SECURITY;

-- ROW SECURITY: stewardship_setup_sealed_credential
ALTER TABLE public.stewardship_setup_sealed_credential ENABLE ROW LEVEL SECURITY;

-- POLICY: stewardship_sealed_credential_staging stewardship_staging_cleanup
CREATE POLICY stewardship_staging_cleanup ON public.stewardship_sealed_credential_staging FOR DELETE USING ((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)));

-- POLICY: stewardship_sealed_credential_staging stewardship_staging_intake
CREATE POLICY stewardship_staging_intake ON public.stewardship_sealed_credential_staging FOR INSERT WITH CHECK ((CURRENT_USER = 'pk_stewardship_web'::name));

-- POLICY: stewardship_sealed_credential_staging stewardship_staging_read
CREATE POLICY stewardship_staging_read ON public.stewardship_sealed_credential_staging FOR SELECT USING (((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)) OR (CURRENT_USER = ANY (ARRAY['pk_stewardship_web'::name, 'pk_stewardship_backup_worker'::name]))));

-- POLICY: stewardship_sealed_credential_staging stewardship_staging_scrub
CREATE POLICY stewardship_staging_scrub ON public.stewardship_sealed_credential_staging FOR UPDATE USING ((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text))) WITH CHECK ((CURRENT_USER = ('pk_stewardship_credential_'::text || (target)::text)));

-- ACL: FUNCTION stewardship_bootstrap_empty_database()
REVOKE ALL ON FUNCTION public.stewardship_bootstrap_empty_database() FROM PUBLIC;

-- Phase 3A response references and indexes; all table keys now exist.
ALTER TABLE "stewardship_family_form_baseline" ADD CONSTRAINT "stewardship_family_f_family_id_b74686eb_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_form_baseline" ADD CONSTRAINT "stewardship_family_f_source_id_a45560e8_fk_stewardsh" FOREIGN KEY ("source_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_form_baseline" ADD CONSTRAINT "stewardship_family_f_configuration_id_215927b6_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_family_form_baseline" ADD CONSTRAINT "stewardship_family_f_prior_submission_id_25bb3f1e_fk_stewardsh" FOREIGN KEY ("prior_submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_family_form_baseline_correlation_id_df990299" ON "stewardship_family_form_baseline" ("correlation_id");
CREATE INDEX "stewardship_family_form_baseline_family_id_b74686eb" ON "stewardship_family_form_baseline" ("family_id");
CREATE INDEX "stewardship_family_form_baseline_source_id_a45560e8" ON "stewardship_family_form_baseline" ("source_id");
CREATE INDEX "stewardship_family_form_baseline_configuration_id_215927b6" ON "stewardship_family_form_baseline" ("configuration_id");
CREATE INDEX "stewardship_family_form_baseline_prior_submission_id_25bb3f1e" ON "stewardship_family_form_baseline" ("prior_submission_id");
CREATE INDEX "family_baseline_expiry" ON "stewardship_family_form_baseline" ("state", "expires_at");
CREATE INDEX "family_baseline_session" ON "stewardship_family_form_baseline" ("family_session_id", "state");
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_family_id_c36ec6ca_fk_stewardsh" FOREIGN KEY ("family_id") REFERENCES "stewardship_family_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_campaign_id_3f80f67e_fk_stewardsh" FOREIGN KEY ("campaign_id") REFERENCES "stewardship_campaign" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_baseline_id_0b332131_fk_stewardsh" FOREIGN KEY ("baseline_id") REFERENCES "stewardship_family_form_baseline" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_reviewed_source_id_7693938b_fk_stewardsh" FOREIGN KEY ("reviewed_source_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_validation_source_id_dccb0988_fk_stewardsh" FOREIGN KEY ("validation_source_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_configuration_id_850edd92_fk_stewardsh" FOREIGN KEY ("configuration_id") REFERENCES "stewardship_configuration_version" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_submission" ADD CONSTRAINT "stewardship_submissi_prior_submission_id_791966fe_fk_stewardsh" FOREIGN KEY ("prior_submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_submission_correlation_id_f9d4aaa9" ON "stewardship_submission" ("correlation_id");
CREATE INDEX "stewardship_submission_family_id_c36ec6ca" ON "stewardship_submission" ("family_id");
CREATE INDEX "stewardship_submission_campaign_id_3f80f67e" ON "stewardship_submission" ("campaign_id");
CREATE INDEX "stewardship_submission_reviewed_source_id_7693938b" ON "stewardship_submission" ("reviewed_source_id");
CREATE INDEX "stewardship_submission_validation_source_id_dccb0988" ON "stewardship_submission" ("validation_source_id");
CREATE INDEX "stewardship_submission_configuration_id_850edd92" ON "stewardship_submission" ("configuration_id");
CREATE INDEX "stewardship_submission_prior_submission_id_791966fe" ON "stewardship_submission" ("prior_submission_id");
CREATE INDEX "submission_family_time" ON "stewardship_submission" ("family_id", "mode", "submitted_at");
CREATE INDEX "submission_local_day" ON "stewardship_submission" ("mode", "submitted_on");
ALTER TABLE "stewardship_proposed_change" ADD CONSTRAINT "stewardship_proposed_submission_id_ccbe7500_fk_stewardsh" FOREIGN KEY ("submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_proposed_change" ADD CONSTRAINT "stewardship_proposed_current_source_id_65846641_fk_stewardsh" FOREIGN KEY ("current_source_id") REFERENCES "stewardship_source_snapshot" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_proposed_change" ADD CONSTRAINT "stewardship_proposed_superseded_by_id_6f86a133_fk_stewardsh" FOREIGN KEY ("superseded_by_id") REFERENCES "stewardship_proposed_change" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_proposed_change_correlation_id_1848c771" ON "stewardship_proposed_change" ("correlation_id");
CREATE INDEX "stewardship_proposed_change_submission_id_ccbe7500" ON "stewardship_proposed_change" ("submission_id");
CREATE INDEX "stewardship_proposed_change_current_source_id_65846641" ON "stewardship_proposed_change" ("current_source_id");
CREATE INDEX "stewardship_proposed_change_superseded_by_id_6f86a133" ON "stewardship_proposed_change" ("superseded_by_id");
CREATE INDEX "proposal_review_queue" ON "stewardship_proposed_change" ("decision", "execution");
ALTER TABLE "stewardship_additional_information" ADD CONSTRAINT "stewardship_addition_submission_id_4e9b4c92_fk_stewardsh" FOREIGN KEY ("submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_additional_information" ADD CONSTRAINT "stewardship_addition_replacement_id_e7eb6029_fk_stewardsh" FOREIGN KEY ("replacement_id") REFERENCES "stewardship_additional_information" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_additional_information_correlation_id_bec7a412" ON "stewardship_additional_information" ("correlation_id");
CREATE INDEX "additional_actionable_queue" ON "stewardship_additional_information" ("disposition", "created_at");
ALTER TABLE "stewardship_submission_receipt" ADD CONSTRAINT "stewardship_submissi_submission_id_cae3467e_fk_stewardsh" FOREIGN KEY ("submission_id") REFERENCES "stewardship_submission" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX "stewardship_submission_receipt_correlation_id_86526335" ON "stewardship_submission_receipt" ("correlation_id");

-- Form metadata cannot be rebound to another session, input set or Family.
-- The common advisory order serializes admission with source/lifecycle writes.
CREATE FUNCTION public.stewardship_family_baseline_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    session_row public.stewardship_family_session;
    runtime_row public.stewardship_system_configuration;
    family_row public.stewardship_family_campaign;
    campaign_row public.stewardship_campaign;
    campaign_config public.stewardship_campaign_configuration;
    credential_row public.stewardship_campaign_credentials;
    instant timestamptz := clock_timestamp();
    previous_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'
          AND classid=736220 AND objid=1 AND objsubid=2
          AND mode='ExclusiveLock' AND granted
    ) THEN
        RAISE EXCEPTION 'Form metadata requires ordered admission' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF public.stewardship_test_response_cleanup_v1(OLD.mode,OLD.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Form metadata deletion requires its retention owner' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.state <> 'open' OR NEW.state = 'open'
           OR NEW.version <> OLD.version + 1
           OR (to_jsonb(NEW) - ARRAY['state','ended_at','version','updated_at'])
                IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['state','ended_at','version','updated_at'])
           OR NEW.ended_at > instant THEN
            RAISE EXCEPTION 'Form metadata bindings are immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.state = 'expired' AND NEW.expires_at > instant THEN
            RAISE EXCEPTION 'The form has not expired' USING ERRCODE='23514';
        END IF;
        NEW.updated_at := instant;
        RETURN NEW;
    END IF;
    SELECT * INTO runtime_row FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO session_row FROM public.stewardship_family_session
        WHERE id=NEW.family_session_id FOR UPDATE;
    IF NOT FOUND OR session_row.family_id <> NEW.family_id
       OR session_row.revoked_at IS NOT NULL
       OR instant >= session_row.expires_at
       OR instant >= session_row.last_activity_at + interval '60 minutes'
       OR session_row.mode <> runtime_row.mode
       OR session_row.rehearsal_epoch_id IS DISTINCT FROM NEW.rehearsal_epoch_id
       OR NEW.mode <> (CASE session_row.mode WHEN 'testing' THEN 'test' ELSE 'live' END)
       OR NEW.expires_at > session_row.expires_at
       OR NEW.expires_at <= instant OR NEW.state <> 'open'
       OR NEW.version <> 1 OR NEW.actor_id IS DISTINCT FROM NEW.family_id
       OR NEW.configuration_id IS DISTINCT FROM runtime_row.active_configuration_id
       OR runtime_row.restore_review_required
       OR session_row.credential_epoch IS DISTINCT FROM
           (SELECT family_link_epoch FROM public.stewardship_credential_deployment)
    THEN
        RAISE EXCEPTION 'Form session admission is unavailable' USING ERRCODE='23514';
    END IF;
    SELECT * INTO family_row FROM public.stewardship_family_campaign
        WHERE id=NEW.family_id FOR UPDATE;
    SELECT * INTO campaign_row FROM public.stewardship_campaign
        WHERE id=family_row.campaign_id;
    SELECT * INTO campaign_config FROM public.stewardship_campaign_configuration
        WHERE id=campaign_row.active_configuration_id;
    SELECT * INTO credential_row FROM public.stewardship_campaign_credentials
        WHERE campaign_id=campaign_row.id;
    IF family_row.id IS NULL OR NOT family_row.portal_eligible
       OR campaign_row.id IS DISTINCT FROM runtime_row.current_campaign_id
       OR campaign_config.id IS NULL OR credential_row.id IS NULL
       OR credential_row.go_live_gate OR credential_row.population_dirty
       OR NOT (campaign_config.starts_at <= public.stewardship_campaign_now_v1()
               AND public.stewardship_campaign_now_v1() < campaign_config.ends_at)
       OR (NEW.mode='live' AND campaign_row.state NOT IN ('scheduled','active'))
       OR (NEW.mode='test' AND (campaign_row.state <> 'draft'
           OR credential_row.rehearsal_epoch_id IS DISTINCT FROM NEW.rehearsal_epoch_id
           OR NOT EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch
                          WHERE id=NEW.rehearsal_epoch_id AND state='active')))
       OR NOT EXISTS (
           SELECT 1 FROM public.stewardship_source_current current_source
             JOIN public.stewardship_source_snapshot snapshot ON snapshot.id=current_source.snapshot_id
           WHERE snapshot.id=NEW.source_id AND snapshot.state='promoted'
             AND snapshot.compacted_at IS NULL
       )
    THEN
        RAISE EXCEPTION 'Form campaign or source admission is unavailable' USING ERRCODE='23514';
    END IF;
    SELECT id INTO previous_id FROM public.stewardship_submission
        WHERE family_id=NEW.family_id AND mode=NEW.mode
          AND rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
        ORDER BY family_version DESC LIMIT 1;
    IF previous_id IS DISTINCT FROM NEW.prior_submission_id THEN
        RAISE EXCEPTION 'The effective Family response changed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stewardship_family_baseline_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_family_form_baseline
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_family_baseline_guard_v1();

-- Deferred validation lets insertion/pinning and transition/unpinning commit
-- together. An open baseline never survives without its exact expiring pin.
CREATE FUNCTION public.stewardship_family_baseline_pin_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    baseline public.stewardship_family_form_baseline;
BEGIN
    SELECT * INTO baseline FROM public.stewardship_family_form_baseline WHERE id=NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF baseline.state = 'open' AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_source_pin
        WHERE snapshot_id=baseline.source_id AND parent_kind='form_baseline'
          AND parent_id=baseline.id AND expires_at=baseline.expires_at
    ) THEN
        RAISE EXCEPTION 'Open form baseline requires its source protection' USING ERRCODE='23514';
    END IF;
    IF baseline.state <> 'open' AND EXISTS (
        SELECT 1 FROM public.stewardship_source_pin
        WHERE parent_kind='form_baseline' AND parent_id=baseline.id
    ) THEN
        RAISE EXCEPTION 'Ended form baseline still owns expiring protection' USING ERRCODE='23514';
    END IF;
    IF (baseline.state='submitted') IS DISTINCT FROM EXISTS (
        SELECT 1 FROM public.stewardship_submission WHERE baseline_id=baseline.id
    ) THEN
        RAISE EXCEPTION 'Submitted form requires its immutable response' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER stewardship_family_baseline_pin
    AFTER INSERT OR UPDATE ON public.stewardship_family_form_baseline
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_family_baseline_pin_v1();

-- The opposite side of the reference must also preserve a still-live baseline.
-- Expired baselines are inadmissible even before bounded housekeeping catches up.
CREATE FUNCTION public.stewardship_family_pin_reference_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    baseline public.stewardship_family_form_baseline;
BEGIN
    IF current_user='pk_stewardship_worker' THEN
        -- Workers can release only superseded comparison pins, never invoke the
        -- separate privileged rehearsal-retention exception or read its data.
        IF public.stewardship_response_pin_required_v1(OLD.parent_id,OLD.snapshot_id)
           AND NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin
               WHERE parent_kind='submission' AND parent_id=OLD.parent_id
                 AND snapshot_id=OLD.snapshot_id AND expires_at IS NULL)
        THEN
            RAISE EXCEPTION 'Retained response requires its source protection' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
    END IF;
    IF OLD.parent_kind='submission' AND EXISTS (
        SELECT 1 FROM public.stewardship_submission response
        WHERE response.id=OLD.parent_id
          AND NOT public.stewardship_test_response_cleanup_v1(response.mode,response.rehearsal_epoch_id)
          AND (
            OLD.snapshot_id IN (response.reviewed_source_id,response.validation_source_id)
            OR EXISTS (SELECT 1 FROM public.stewardship_proposed_change
                       WHERE submission_id=response.id AND current_source_id=OLD.snapshot_id))
    ) AND NOT EXISTS (
        SELECT 1 FROM public.stewardship_source_pin
        WHERE parent_kind='submission' AND parent_id=OLD.parent_id
          AND snapshot_id=OLD.snapshot_id AND expires_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Retained response requires its source protection' USING ERRCODE='23514';
    END IF;
    IF OLD.parent_kind <> 'form_baseline' THEN RETURN NULL; END IF;
    SELECT * INTO baseline FROM public.stewardship_family_form_baseline
        WHERE id=OLD.parent_id;
    IF FOUND AND baseline.state='open' AND baseline.expires_at > clock_timestamp()
       AND NOT EXISTS (
           SELECT 1 FROM public.stewardship_source_pin
           WHERE snapshot_id=baseline.source_id AND parent_kind='form_baseline'
             AND parent_id=baseline.id AND expires_at=baseline.expires_at
       ) THEN
        RAISE EXCEPTION 'Live form baseline requires its source protection' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER stewardship_family_pin_reference
    AFTER UPDATE OR DELETE ON public.stewardship_source_pin
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_family_pin_reference_v1();

-- A final response owns immutable history and one complete local effects batch.
CREATE FUNCTION public.stewardship_submission_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    baseline public.stewardship_family_form_baseline;
    session_row public.stewardship_family_session;
    runtime_row public.stewardship_system_configuration;
    campaign_row public.stewardship_campaign;
    config_row public.stewardship_campaign_configuration;
    previous_id uuid;
    previous_version bigint;
    last_sequence bigint;
    member_key text;
    member_answer jsonb;
    member_proposed boolean;
    birth_value jsonb;
    field_name text;
    field_value jsonb;
    source_value jsonb;
    display_value text;
    allowed_fields text[] := ARRAY['prefix','first_name','middle_name','last_name','suffix','nickname',
        'maiden_name','birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language'];
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF TG_OP='DELETE' AND public.stewardship_test_response_cleanup_v1(OLD.mode,OLD.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Submission history is immutable' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Submission requires ordered admission' USING ERRCODE='23514';
    END IF;
    SELECT * INTO baseline FROM public.stewardship_family_form_baseline WHERE id=NEW.baseline_id FOR UPDATE;
    SELECT * INTO runtime_row FROM public.stewardship_system_configuration FOR UPDATE;
    SELECT * INTO session_row FROM public.stewardship_family_session WHERE id=baseline.family_session_id FOR UPDATE;
    SELECT * INTO campaign_row FROM public.stewardship_campaign WHERE id=NEW.campaign_id FOR UPDATE;
    SELECT * INTO config_row FROM public.stewardship_campaign_configuration WHERE id=campaign_row.active_configuration_id;
    -- The insertion statement owns the business instant; a Python preflight
    -- timestamp is not equal to the later INSERT's statement timestamp.
    NEW.submitted_at := public.stewardship_campaign_now_v1();
    NEW.submitted_on := (NEW.submitted_at AT TIME ZONE config_row.timezone)::date;
    IF baseline.id IS NULL OR baseline.state <> 'open' OR baseline.expires_at <= clock_timestamp()
        OR session_row.id IS NULL OR session_row.revoked_at IS NOT NULL
        OR session_row.expires_at <= clock_timestamp()
        OR session_row.last_activity_at + interval '60 minutes' <= clock_timestamp()
        OR NEW.family_id IS DISTINCT FROM baseline.family_id
        OR NEW.family_id IS DISTINCT FROM session_row.family_id
        OR NEW.actor_id IS DISTINCT FROM NEW.family_id
        OR NEW.mode IS DISTINCT FROM baseline.mode
        OR NEW.rehearsal_epoch_id IS DISTINCT FROM baseline.rehearsal_epoch_id
        OR NEW.rehearsal_epoch_id IS DISTINCT FROM session_row.rehearsal_epoch_id
        OR NEW.prior_submission_id IS DISTINCT FROM baseline.prior_submission_id
        OR NEW.reviewed_source_id IS DISTINCT FROM baseline.source_id
        OR NEW.form_schema IS DISTINCT FROM baseline.form_schema
        OR NEW.form_schema <> 'family-census-household-members-v1'
        OR NEW.configuration_id IS DISTINCT FROM runtime_row.active_configuration_id
        OR runtime_row.restore_review_required OR session_row.mode <> runtime_row.mode
        OR NEW.campaign_id IS DISTINCT FROM runtime_row.current_campaign_id
        OR config_row.id IS NULL OR campaign_row.id IS NULL
        OR NEW.submitted_on IS DISTINCT FROM (NEW.submitted_at AT TIME ZONE config_row.timezone)::date
        OR NEW.submitted_at < config_row.starts_at OR NEW.submitted_at >= config_row.ends_at
        OR NEW.annual_pledge IS NOT NULL
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign
            WHERE id=NEW.family_id AND campaign_id=NEW.campaign_id AND portal_eligible)
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_current
            WHERE snapshot_id=NEW.validation_source_id)
        OR session_row.credential_epoch IS DISTINCT FROM
            (SELECT family_link_epoch FROM public.stewardship_credential_deployment)
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_credentials credentials
            WHERE credentials.campaign_id=NEW.campaign_id AND NOT go_live_gate AND NOT population_dirty
              AND ((NEW.mode='live' AND runtime_row.mode='production' AND campaign_row.state IN ('scheduled','active'))
               OR (NEW.mode='test' AND runtime_row.mode='testing' AND campaign_row.state='draft'
                  AND credentials.rehearsal_epoch_id=NEW.rehearsal_epoch_id
                  AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch
                      WHERE id=NEW.rehearsal_epoch_id AND state='active'))))
    THEN
        RAISE EXCEPTION 'Submission provenance or admission is unavailable' USING ERRCODE='23514';
    END IF;
    SELECT id,family_version INTO previous_id,previous_version FROM public.stewardship_submission
        WHERE family_id=NEW.family_id AND mode=NEW.mode
          AND rehearsal_epoch_id IS NOT DISTINCT FROM NEW.rehearsal_epoch_id
        ORDER BY family_version DESC LIMIT 1;
    SELECT coalesce(max(family_version),0) INTO previous_version FROM public.stewardship_submission
        WHERE family_id=NEW.family_id AND mode=NEW.mode;
    SELECT coalesce(max(campaign_sequence),0) INTO last_sequence FROM public.stewardship_submission
        WHERE campaign_id=NEW.campaign_id AND mode=NEW.mode;
    IF NEW.prior_submission_id IS DISTINCT FROM previous_id
        OR NEW.family_version <> coalesce(previous_version,0)+1
        OR NEW.campaign_sequence <> last_sequence+1
        OR jsonb_typeof(NEW.answers) <> 'object'
        OR NOT (NEW.answers ?& ARRAY['schema','family','members','proposed_members','additional_information'])
        OR NEW.answers - ARRAY['schema','family','members','proposed_members','additional_information'] <> '{}'::jsonb
        OR NEW.answers->>'schema' IS DISTINCT FROM NEW.form_schema
        OR jsonb_typeof(NEW.answers->'family') <> 'object'
        OR jsonb_typeof(NEW.answers->'members') <> 'object'
        OR jsonb_typeof(NEW.answers->'proposed_members') <> 'object'
        OR jsonb_typeof(NEW.answers->'additional_information') <> 'string'
        OR length(NEW.answers->>'additional_information') > 5000
    THEN
        RAISE EXCEPTION 'Submission aggregate or sequence is invalid' USING ERRCODE='23514';
    END IF;
    IF NOT ((NEW.answers->'family') ?& ARRAY['home_address','mailing_address','email_opt_out','mailing_same_as_home'])
       OR (NEW.answers->'family')-ARRAY['home_address','mailing_address','email_opt_out','mailing_same_as_home'] <> '{}'::jsonb
       OR jsonb_typeof(NEW.answers#>'{family,mailing_same_as_home}') IS DISTINCT FROM 'boolean'
    THEN
        RAISE EXCEPTION 'Submission household aggregate is invalid' USING ERRCODE='23514';
    END IF;
    PERFORM public.stewardship_response_address_guard_v1(NEW.answers#>'{family,home_address}');
    PERFORM public.stewardship_response_address_guard_v1(NEW.answers#>'{family,mailing_address}');
    PERFORM public.stewardship_response_comparison_v1('email_opt_out',NEW.answers#>'{family,email_opt_out}');
    -- A response is a complete active household, not a patch or a caller-chosen
    -- list. These checks also protect direct exact-role writes outside Python.
    IF (SELECT coalesce(array_agg(key ORDER BY key),'{}'::text[]) FROM jsonb_object_keys(NEW.answers->'members') key)
       IS DISTINCT FROM (
           SELECT coalesce(array_agg(membership.source_key::text ORDER BY membership.source_key),'{}'::text[])
           FROM public.stewardship_snapshot_member membership
           JOIN public.stewardship_source_member member ON member.id=membership.payload_id
           JOIN public.stewardship_family_campaign family ON member.family_key=family.family_duid::text
           WHERE membership.snapshot_id=NEW.validation_source_id AND family.id=NEW.family_id
             AND member.canonical::jsonb->'active'='true'::jsonb
             AND member.canonical::jsonb->'deceased'='false'::jsonb) THEN
        RAISE EXCEPTION 'Submission must include the current active household' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(NEW.answers->'proposed_members')) > 100 THEN
        RAISE EXCEPTION 'Too many proposed household Members' USING ERRCODE='23514';
    END IF;
    FOR member_key,member_answer,member_proposed IN
        SELECT key,value,false FROM jsonb_each(NEW.answers->'members')
        UNION ALL SELECT key,value,true FROM jsonb_each(NEW.answers->'proposed_members') LOOP
        IF member_proposed AND (member_key !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR member_key='00000000-0000-0000-0000-000000000000') THEN
            RAISE EXCEPTION 'Proposed Member requires canonical local identity' USING ERRCODE='23514';
        END IF;
        IF NOT member_proposed AND jsonb_typeof(member_answer)='object'
           AND (member_answer ? 'moved_household' OR member_answer ? 'deceased_status') THEN
            IF member_answer->'confirmed' IS DISTINCT FROM 'true'::jsonb THEN
                RAISE EXCEPTION 'Terminal Member requires explicit confirmation' USING ERRCODE='23514';
            END IF;
            IF member_answer ? 'moved_household' THEN
                IF member_answer IS DISTINCT FROM '{"moved_household":true,"confirmed":true}'::jsonb THEN
                    RAISE EXCEPTION 'Moved household request has unexpected fields' USING ERRCODE='23514';
                END IF;
            ELSE
                IF NOT (member_answer ?& ARRAY['deceased_status','confirmed','death_date'])
                   OR member_answer-ARRAY['deceased_status','confirmed','death_date'] <> '{}'::jsonb
                   OR member_answer->'deceased_status' IS DISTINCT FROM 'true'::jsonb THEN
                    RAISE EXCEPTION 'Deceased request has unexpected fields' USING ERRCODE='23514';
                END IF;
                PERFORM public.stewardship_response_comparison_v1('death_date',member_answer->'death_date');
                birth_value := public.stewardship_response_field_source_v1(
                    NEW.validation_source_id,NEW.family_id,member_key,'birth_date');
                IF member_answer->'death_date'<>'null'::jsonb AND (
                    (member_answer->>'death_date')::date>NEW.submitted_on
                    OR (birth_value->'available'='true'::jsonb AND birth_value->'value'<>'null'::jsonb
                        AND (member_answer->>'death_date')::date<(birth_value->>'value')::date)) THEN
                    RAISE EXCEPTION 'Death date must respect recorded birth and parish day' USING ERRCODE='23514';
                END IF;
            END IF;
            CONTINUE;
        END IF;
        IF jsonb_typeof(member_answer)<>'object' OR NOT (member_answer ?& allowed_fields)
           OR member_answer-allowed_fields<>'{}'::jsonb THEN
            RAISE EXCEPTION 'Submission member fields are incomplete' USING ERRCODE='23514';
        END IF;
        FOR field_name,field_value IN SELECT * FROM jsonb_each(member_answer) LOOP
            source_value := CASE WHEN member_proposed THEN jsonb_build_object('available',false,'value',NULL)
                ELSE public.stewardship_response_field_source_v1(
                    NEW.validation_source_id,NEW.family_id,member_key,field_name) END;
            PERFORM public.stewardship_response_comparison_v1(field_name,field_value);
            IF field_name='birth_date' THEN
                IF field_value<>'null'::jsonb AND (field_value#>>'{}')::date>NEW.submitted_on THEN
                    RAISE EXCEPTION 'Member birth date cannot be in the future' USING ERRCODE='23514';
                END IF;
                CONTINUE;
            END IF;
            IF field_value='null'::jsonb THEN
                IF field_name IN ('first_name','last_name','gender','language')
                   OR (source_value->'available'='true'::jsonb AND source_value->'value'<>'null'::jsonb) THEN
                    RAISE EXCEPTION 'Member nullable field has no supported blank source' USING ERRCODE='23514';
                END IF;
                CONTINUE;
            END IF;
            display_value := CASE WHEN field_name IN ('home_phone','mobile_phone','work_phone')
                                  AND jsonb_typeof(field_value)='object'
                THEN field_value->>'display' ELSE field_value#>>'{}' END;
            IF length(display_value)>(CASE WHEN field_name='email' THEN 254 ELSE 100 END)
               OR display_value IS DISTINCT FROM public.stewardship_response_comparison_v1('first_name',to_jsonb(display_value))
               OR display_value ~ U&'[\0001-\001F\007F\0085\2028\2029]'
               OR (field_name IN ('first_name','last_name','gender','language') AND display_value='') THEN
                RAISE EXCEPTION 'Member text field is not normalized or complete' USING ERRCODE='23514';
            END IF;
            IF field_name='gender' AND display_value NOT IN ('Male','Female','Unspecified')
               AND public.stewardship_response_comparison_v1(field_name,field_value)
                   IS DISTINCT FROM public.stewardship_response_comparison_v1(field_name,source_value->'value') THEN
                RAISE EXCEPTION 'Member gender choice is invalid' USING ERRCODE='23514';
            END IF;
            IF field_name='marital_status' AND display_value NOT IN ('','Annulled','Divorced','Married','Single','Separated','Widowed')
               AND public.stewardship_response_comparison_v1(field_name,field_value)
                   IS DISTINCT FROM public.stewardship_response_comparison_v1(field_name,source_value->'value') THEN
                RAISE EXCEPTION 'Member marital choice is invalid' USING ERRCODE='23514';
            END IF;
            IF field_name IN ('home_phone','mobile_phone','work_phone') AND display_value<>''
               AND field_value IS DISTINCT FROM source_value->'value'
               AND coalesce(field_value->>'normalized','') !~ '^\+[1-9][0-9]{1,14}(;ext=[0-9]{1,12})?$' THEN
                RAISE EXCEPTION 'Member new phone must have an international comparison number' USING ERRCODE='23514';
            END IF;
        END LOOP;
    END LOOP;
    IF (NEW.answers#>>'{family,mailing_same_as_home}')::boolean
       AND (NEW.answers#>'{family,home_address}'='null'::jsonb
            OR public.stewardship_response_comparison_v1('home_address',NEW.answers#>'{family,home_address}')
               IS DISTINCT FROM public.stewardship_response_comparison_v1('mailing_address',NEW.answers#>'{family,mailing_address}'))
    THEN
        RAISE EXCEPTION 'Submission mailing choice requires matching addresses' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stewardship_submission_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.stewardship_submission
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_submission_guard_v1();

CREATE FUNCTION public.stewardship_submission_effects_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_submission WHERE id=NEW.id) THEN RETURN NULL; END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_family_form_baseline
                  WHERE id=NEW.baseline_id AND state='submitted')
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_submission_receipt WHERE submission_id=NEW.id)
       OR EXISTS (
           SELECT source_id FROM (VALUES (NEW.reviewed_source_id),(NEW.validation_source_id)) inputs(source_id)
           WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin
               WHERE parent_kind='submission' AND parent_id=NEW.id AND snapshot_id=inputs.source_id AND expires_at IS NULL)
       ) THEN
        RAISE EXCEPTION 'Submission requires atomic baseline, receipt and source protection' USING ERRCODE='23514';
    END IF;
    IF NEW.mode='live' AND (
        NOT EXISTS (SELECT 1 FROM public.stewardship_family_campaign family
            WHERE family.id=NEW.family_id
              AND family.first_live_submission_id=(SELECT id FROM public.stewardship_submission
                WHERE family_id=NEW.family_id AND mode='live' ORDER BY family_version LIMIT 1)
              AND family.effective_submission_id=(SELECT id FROM public.stewardship_submission
                WHERE family_id=NEW.family_id AND mode='live' ORDER BY family_version DESC LIMIT 1))
        OR NOT EXISTS (SELECT 1 FROM public.stewardship_campaign
                       WHERE id=NEW.campaign_id AND first_live_submission_at IS NOT NULL)
        OR EXISTS (SELECT scope FROM (VALUES ('historical'),('current')) populations(scope)
            WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_fact_demand
                WHERE campaign_id=NEW.campaign_id AND population_scope=populations.scope
                  AND requested_submission_watermark >= NEW.campaign_sequence))
    ) THEN
        RAISE EXCEPTION 'Live response requires atomic selectors and fact demand' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER stewardship_submission_effects
    AFTER INSERT ON public.stewardship_submission DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_submission_effects_v1();

CREATE FUNCTION public.stewardship_response_derived_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    response public.stewardship_submission;
    predecessor public.stewardship_proposed_change;
    source_field jsonb;
    expected_baseline jsonb;
    carries_intent boolean;
    submitted_key text;
    baseline_key text;
    current_key text;
    expected_execution text;
    expected_submitted jsonb;
    expected_handling text;
BEGIN
    IF TG_OP='DELETE' THEN
        SELECT * INTO response FROM public.stewardship_submission WHERE id=OLD.submission_id;
        IF FOUND AND public.stewardship_test_response_cleanup_v1(response.mode,response.rehearsal_epoch_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'Response detail deletion requires its retention owner' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Response detail requires ordered admission' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME <> 'stewardship_proposed_change' OR TG_OP='INSERT' THEN
        SELECT * INTO response FROM public.stewardship_submission WHERE id=NEW.submission_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Response detail has no immutable parent' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_TABLE_NAME='stewardship_submission_receipt' THEN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Receipt intent is immutable' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_TABLE_NAME='stewardship_proposed_change' THEN
        IF TG_OP='INSERT' THEN
            IF NEW.entity_kind='member' AND NEW.field IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname','maiden_name',
                'birth_date','gender','email','home_phone','mobile_phone','work_phone','marital_status','language',
                'moved_household','deceased_status','death_date')
               AND (response.answers->'members') ? NEW.entity_key
               AND (response.answers#>ARRAY['members',NEW.entity_key]) ? NEW.field THEN
                expected_submitted := response.answers#>ARRAY['members',NEW.entity_key,NEW.field];
                IF NEW.field='death_date' AND expected_submitted='null'::jsonb THEN
                    RAISE EXCEPTION 'Omitted terminal date cannot request source clearing' USING ERRCODE='23514';
                END IF;
                expected_handling := CASE WHEN NEW.field IN ('prefix','suffix','marital_status','moved_household','deceased_status')
                    THEN 'manual' ELSE 'api' END;
                source_field := public.stewardship_response_field_source_v1(
                    response.validation_source_id,response.family_id,NEW.entity_key,NEW.field);
            ELSIF NEW.entity_kind='proposed_member' AND NEW.field='new_member'
                  AND (response.answers->'proposed_members') ? NEW.entity_key THEN
                expected_submitted := response.answers#>ARRAY['proposed_members',NEW.entity_key];
                expected_handling := 'manual';
                source_field := jsonb_build_object('available',false,'value',NULL);
            ELSIF NEW.entity_kind='family' AND NEW.field IN ('home_address','mailing_address','email_opt_out') THEN
                expected_submitted := response.answers#>ARRAY['family',NEW.field];
                expected_handling := CASE WHEN NEW.field='email_opt_out' THEN 'manual' ELSE 'api' END;
                source_field := public.stewardship_response_household_source_v1(
                    response.validation_source_id,response.family_id,NEW.entity_key,NEW.field);
            ELSE
                RAISE EXCEPTION 'Proposal field has no admitted response owner' USING ERRCODE='23514';
            END IF;
            IF coalesce(NEW.submitted_value,'null'::jsonb) IS DISTINCT FROM expected_submitted
                OR NEW.actor_id IS DISTINCT FROM response.family_id
                OR NEW.current_source_id IS DISTINCT FROM response.validation_source_id
                OR NEW.handling IS DISTINCT FROM expected_handling THEN
                RAISE EXCEPTION 'Proposal differs from its immutable response' USING ERRCODE='23514';
            END IF;
            IF NEW.execution NOT IN ('pending','conflict') OR NEW.superseded_by_id IS NOT NULL THEN
                RAISE EXCEPTION 'New proposal cannot mint execution outcomes' USING ERRCODE='23514';
            END IF;
            IF source_field IS NULL OR source_field IS DISTINCT FROM
                jsonb_build_object('available',NEW.current_available,'value',NEW.current_value)
            THEN
                RAISE EXCEPTION 'Proposal comparison differs from its validation source' USING ERRCODE='23514';
            END IF;
            -- Terminal/date work may survive an intervening response that has
            -- no active source Member to display. Mirror the Family namespace
            -- and latest-record selection used by effective.proposal_index.
            SELECT candidate.* INTO predecessor FROM public.stewardship_proposed_change candidate
            JOIN public.stewardship_submission earlier ON earlier.id=candidate.submission_id
            JOIN public.stewardship_submission prior ON prior.id=response.prior_submission_id
            WHERE earlier.family_id=response.family_id AND earlier.campaign_id=response.campaign_id
              AND earlier.mode=response.mode
              AND earlier.rehearsal_epoch_id IS NOT DISTINCT FROM response.rehearsal_epoch_id
              AND earlier.family_version<=prior.family_version
              AND (earlier.id=prior.id OR (NEW.entity_kind='member'
                   AND NEW.field IN ('moved_household','deceased_status','death_date')))
              AND ROW(candidate.entity_kind,candidate.entity_key,candidate.field)=ROW(NEW.entity_kind,NEW.entity_key,NEW.field)
            ORDER BY earlier.family_version DESC LIMIT 1;
            carries_intent := FOUND
                AND predecessor.execution NOT IN ('published','resolved_upstream','resolved_external','cancelled','superseded') AND (
                public.stewardship_response_comparison_v1(NEW.field,NEW.submitted_value)
                IS NOT DISTINCT FROM public.stewardship_response_comparison_v1(NEW.field,predecessor.submitted_value));
            expected_baseline := source_field;
            IF carries_intent THEN
                expected_baseline := jsonb_build_object('available',predecessor.baseline_available,'value',predecessor.baseline_value);
                IF ROW(NEW.decision,NEW.admin_value_set,NEW.admin_value)
                    IS DISTINCT FROM ROW(predecessor.decision,predecessor.admin_value_set,predecessor.admin_value) THEN
                    RAISE EXCEPTION 'Proposal review state requires the same prior intent' USING ERRCODE='23514';
                END IF;
            ELSIF NEW.decision <> 'unreviewed' OR NEW.admin_value_set OR NEW.admin_value IS NOT NULL THEN
                RAISE EXCEPTION 'New proposal review state must be unreviewed' USING ERRCODE='23514';
            END IF;
            IF expected_baseline IS DISTINCT FROM jsonb_build_object('available',NEW.baseline_available,'value',NEW.baseline_value) THEN
                RAISE EXCEPTION 'Proposal baseline requires exact source or prior intent' USING ERRCODE='23514';
            END IF;
            submitted_key := public.stewardship_response_comparison_v1(NEW.field,NEW.submitted_value);
            baseline_key := public.stewardship_response_comparison_v1(NEW.field,NEW.baseline_value);
            current_key := public.stewardship_response_comparison_v1(NEW.field,NEW.current_value);
            IF (NEW.current_available AND submitted_key IS NOT DISTINCT FROM current_key)
               OR (NOT NEW.current_available AND submitted_key IS NULL)
               OR (NEW.baseline_available AND submitted_key IS NOT DISTINCT FROM baseline_key)
            THEN
                RAISE EXCEPTION 'Unchanged values cannot create actionable proposals' USING ERRCODE='23514';
            END IF;
            expected_execution := CASE
                WHEN NEW.current_available AND (NOT NEW.baseline_available OR current_key IS DISTINCT FROM baseline_key)
                THEN 'conflict' ELSE 'pending' END;
            IF NEW.execution <> expected_execution THEN
                RAISE EXCEPTION 'Proposal execution differs from its derived merge state' USING ERRCODE='23514';
            END IF;
        ELSE
            IF OLD.execution IN ('published','resolved_upstream','resolved_external','cancelled','superseded')
                OR (to_jsonb(NEW)-ARRAY['current_available','current_value','current_source_id','admin_value_set','admin_value','decision','execution','superseded_by_id','version','updated_at'])
                    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['current_available','current_value','current_source_id','admin_value_set','admin_value','decision','execution','superseded_by_id','version','updated_at'])
            THEN
                RAISE EXCEPTION 'Proposal history and terminal outcomes are immutable' USING ERRCODE='23514';
            END IF;
            IF current_user='pk_stewardship_worker' THEN
                IF NEW.execution NOT IN ('pending','conflict','resolved_upstream','cancelled')
                   OR NEW.current_source_id=OLD.current_source_id
                   OR NOT public.stewardship_response_source_owner_v1(NEW.current_source_id)
                   OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_pin
                       WHERE snapshot_id=NEW.current_source_id AND parent_kind='submission'
                         AND parent_id=NEW.submission_id AND expires_at IS NULL)
                THEN
                    RAISE EXCEPTION 'Proposal reconciliation requires fenced protected source' USING ERRCODE='23514';
                END IF;
            ELSIF current_user='pk_stewardship_web' THEN
                IF NEW.execution NOT IN ('superseded','cancelled','resolved_upstream')
                   OR NOT EXISTS (
                       SELECT 1 FROM public.stewardship_submission later
                       JOIN public.stewardship_family_form_baseline baseline ON baseline.id=later.baseline_id
                       JOIN public.stewardship_submission earlier ON earlier.id=OLD.submission_id
                       WHERE baseline.state='open'
                         AND later.family_id=earlier.family_id AND later.campaign_id=earlier.campaign_id
                         AND later.mode=earlier.mode
                         AND later.rehearsal_epoch_id IS NOT DISTINCT FROM earlier.rehearsal_epoch_id
                         AND later.family_version>earlier.family_version
                         AND (later.prior_submission_id=earlier.id OR (OLD.entity_kind='member'
                              AND OLD.field IN ('moved_household','deceased_status','death_date')))
                         AND OLD.id=(
                             SELECT candidate.id FROM public.stewardship_proposed_change candidate
                             JOIN public.stewardship_submission history ON history.id=candidate.submission_id
                             JOIN public.stewardship_submission prior ON prior.id=later.prior_submission_id
                             WHERE history.family_id=later.family_id AND history.campaign_id=later.campaign_id
                               AND history.mode=later.mode
                               AND history.rehearsal_epoch_id IS NOT DISTINCT FROM later.rehearsal_epoch_id
                               AND history.family_version<=prior.family_version
                               AND (history.id=prior.id OR (OLD.entity_kind='member'
                                    AND OLD.field IN ('moved_household','deceased_status','death_date')))
                               AND ROW(candidate.entity_kind,candidate.entity_key,candidate.field)=
                                   ROW(OLD.entity_kind,OLD.entity_key,OLD.field)
                             ORDER BY history.family_version DESC LIMIT 1)
                         AND (NEW.execution <> 'superseded' OR EXISTS (
                             SELECT 1 FROM public.stewardship_proposed_change successor
                             WHERE successor.id=NEW.superseded_by_id AND successor.submission_id=later.id))
                   )
                THEN
                    RAISE EXCEPTION 'Proposal replacement requires a new final Family response' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='stewardship_additional_information' THEN
        IF response.mode <> 'live' OR NEW.text IS DISTINCT FROM response.answers->>'additional_information'
            OR length(NEW.text)>5000 OR btrim(NEW.text)='' THEN
            RAISE EXCEPTION 'Follow-up requires live submitted text' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (
            (to_jsonb(NEW)-ARRAY['disposition','replacement_id','follow_up_needed','followed_up_at','version','updated_at'])
                IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['disposition','replacement_id','follow_up_needed','followed_up_at','version','updated_at'])
            OR (OLD.disposition <> 'current_actionable' AND ROW(NEW.disposition,NEW.replacement_id) IS DISTINCT FROM ROW(OLD.disposition,OLD.replacement_id))
        ) THEN
            RAISE EXCEPTION 'Follow-up text and disposition history are immutable' USING ERRCODE='23514';
        END IF;
    END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.version <> OLD.version+1 THEN
            RAISE EXCEPTION 'Response detail must advance its version' USING ERRCODE='23514';
        END IF;
        NEW.updated_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stewardship_proposed_change_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_proposed_change FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_response_derived_guard_v1();
CREATE TRIGGER stewardship_additional_information_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_additional_information FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_response_derived_guard_v1();
CREATE TRIGGER stewardship_submission_receipt_guard BEFORE INSERT OR UPDATE OR DELETE
    ON public.stewardship_submission_receipt FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_response_derived_guard_v1();

CREATE FUNCTION public.stewardship_family_response_selector_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.first_live_submission_id IS NOT NULL OR NEW.effective_submission_id IS NOT NULL THEN
            RAISE EXCEPTION 'New Family cannot import response selectors' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.first_live_submission_id,NEW.effective_submission_id) IS NOT DISTINCT FROM
       ROW(OLD.first_live_submission_id,OLD.effective_submission_id) THEN RETURN NEW; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted)
       OR NEW.first_live_submission_id IS DISTINCT FROM (
           SELECT id FROM public.stewardship_submission
           WHERE family_id=NEW.id AND mode='live' ORDER BY family_version LIMIT 1)
       OR NEW.effective_submission_id IS DISTINCT FROM (
           SELECT id FROM public.stewardship_submission
           WHERE family_id=NEW.id AND mode='live' ORDER BY family_version DESC LIMIT 1)
       OR NEW.effective_submission_id IS NULL
    THEN
        RAISE EXCEPTION 'Family selectors require current live response evidence' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stewardship_family_response_selector BEFORE INSERT OR UPDATE
    ON public.stewardship_family_campaign FOR EACH ROW
    EXECUTE FUNCTION public.stewardship_family_response_selector_v1();

-- Cross-row response history cannot point into another Family or backwards.
-- Deferred checks permit atomic follow-up replacement in either insert order.
CREATE FUNCTION public.stewardship_response_successor_v1() RETURNS trigger
    LANGUAGE plpgsql SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE actual record;
BEGIN
    IF TG_TABLE_NAME='stewardship_proposed_change' THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_proposed_change proposal
            WHERE proposal.id=NEW.id AND NOT EXISTS (
                SELECT 1 FROM public.stewardship_source_pin
                WHERE snapshot_id=proposal.current_source_id AND parent_kind='submission'
                  AND parent_id=proposal.submission_id AND expires_at IS NULL))
        THEN
            RAISE EXCEPTION 'Proposal comparison requires retained source protection' USING ERRCODE='23514';
        END IF;
        SELECT execution,superseded_by_id INTO actual FROM public.stewardship_proposed_change WHERE id=NEW.id;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF (actual.execution='superseded') IS DISTINCT FROM (actual.superseded_by_id IS NOT NULL) THEN
            RAISE EXCEPTION 'Proposal supersession requires an exact successor' USING ERRCODE='23514';
        END IF;
        IF actual.superseded_by_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1 FROM public.stewardship_proposed_change successor
                JOIN public.stewardship_submission later ON later.id=successor.submission_id
                JOIN public.stewardship_submission earlier ON earlier.id=NEW.submission_id
                WHERE successor.id=actual.superseded_by_id
                  AND ROW(successor.entity_kind,successor.entity_key,successor.field)=ROW(NEW.entity_kind,NEW.entity_key,NEW.field)
                  AND later.family_id=earlier.family_id AND later.mode=earlier.mode
                  AND later.rehearsal_epoch_id IS NOT DISTINCT FROM earlier.rehearsal_epoch_id
                  AND later.family_version>earlier.family_version
            ) THEN
                RAISE EXCEPTION 'Proposal successor has incompatible provenance' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSE
        SELECT disposition,replacement_id INTO actual FROM public.stewardship_additional_information WHERE id=NEW.id;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF actual.replacement_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.stewardship_additional_information successor
            JOIN public.stewardship_submission later ON later.id=successor.submission_id
            JOIN public.stewardship_submission earlier ON earlier.id=NEW.submission_id
            WHERE successor.id=actual.replacement_id AND later.family_id=earlier.family_id
              AND later.mode='live' AND earlier.mode='live'
              AND later.family_version>earlier.family_version
        ) THEN
            RAISE EXCEPTION 'Follow-up replacement has incompatible provenance' USING ERRCODE='23514';
        END IF;
        IF actual.disposition='current_actionable' AND (
            SELECT count(*) FROM public.stewardship_additional_information item
            JOIN public.stewardship_submission response ON response.id=item.submission_id
            WHERE item.disposition='current_actionable' AND response.family_id=(
                SELECT family_id FROM public.stewardship_submission WHERE id=NEW.submission_id)
        ) <> 1 THEN
            RAISE EXCEPTION 'Family has multiple actionable follow-up items' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER stewardship_proposal_successor
    AFTER INSERT OR UPDATE ON public.stewardship_proposed_change DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_response_successor_v1();
CREATE CONSTRAINT TRIGGER stewardship_followup_successor
    AFTER INSERT OR UPDATE ON public.stewardship_additional_information DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_response_successor_v1();
