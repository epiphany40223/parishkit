-- Initial unreleased stewardship schema. See docs/guides/stewardship-schema.md.

-- TABLE: stewardship_activation_catchup
CREATE TABLE public.stewardship_activation_catchup (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    cutoff timestamp with time zone NOT NULL,
    source_snapshot_id uuid,
    phase character varying(32) NOT NULL,
    cursor character varying(128) NOT NULL,
    groups_completed bigint NOT NULL,
    items_completed bigint NOT NULL,
    completed_at timestamp with time zone,
    failure_code character varying(64) NOT NULL,
    campaign_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    task_root_id uuid,
    activation_id uuid NOT NULL,
    CONSTRAINT stewardship_activation_catchup_groups_completed_check CHECK ((groups_completed >= 0)),
    CONSTRAINT stewardship_activation_catchup_items_completed_check CHECK ((items_completed >= 0)),
    CONSTRAINT stewardship_activation_catchup_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_campaigns_activationcatchupdemand_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_address_grant
CREATE TABLE public.stewardship_address_grant (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    role character varying(32) NOT NULL,
    origins jsonb NOT NULL,
    rule_id uuid NOT NULL
);

-- TABLE: stewardship_address_rule
CREATE TABLE public.stewardship_address_rule (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    email character varying(254) NOT NULL,
    roles jsonb NOT NULL,
    creation_origin character varying(16) NOT NULL,
    creation_operation uuid NOT NULL,
    configuration_id uuid NOT NULL
);

-- TABLE: stewardship_admin_revocation
CREATE TABLE public.stewardship_admin_revocation (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    activation_id uuid NOT NULL
);

-- TABLE: stewardship_applied_integration
CREATE TABLE public.stewardship_applied_integration (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    kind character varying(32) NOT NULL,
    settings jsonb NOT NULL,
    credential_fingerprint character varying(64),
    configuration_id uuid NOT NULL,
    CONSTRAINT integration_known_kind CHECK (((kind)::text = ANY ((ARRAY['parishsoft'::character varying, 'google_oauth'::character varying, 'google_workspace'::character varying, 'email'::character varying, 'slack'::character varying, 'backup'::character varying])::text[]))),
    CONSTRAINT integration_safe_fingerprint CHECK (((credential_fingerprint IS NULL) OR ((credential_fingerprint)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: stewardship_assignment_overlay
CREATE TABLE public.stewardship_assignment_overlay (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    assignment_record_id uuid NOT NULL,
    active boolean NOT NULL,
    source_snapshot_id uuid NOT NULL,
    reason character varying(32) NOT NULL,
    CONSTRAINT stewardship_accounts_assignmentoverlay_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_assignment_overlay_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_audit_context
CREATE TABLE public.stewardship_audit_context (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    actor_kind character varying(16) NOT NULL,
    schema character varying(16) NOT NULL,
    context jsonb NOT NULL,
    event_id uuid NOT NULL,
    CONSTRAINT audit_context_actor_kind CHECK (((actor_kind)::text = ANY ((ARRAY['portal_user'::character varying, 'family'::character varying, 'system'::character varying, 'operator'::character varying])::text[]))),
    CONSTRAINT audit_context_schema_safe CHECK ((schema IN ('action','boundary') AND public.stewardship_safe_context_v1((schema)::text, context)))
);

-- TABLE: stewardship_audit_event
CREATE TABLE public.stewardship_audit_event (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    event_type character varying(64) NOT NULL,
    subject_id uuid,
    campaign_reference uuid,
    ownership_scope character varying(16) DEFAULT 'deployment'::character varying NOT NULL,
    parish_id uuid,
    CONSTRAINT audit_event_type_identifier CHECK (((event_type)::text ~ '^[a-z][a-z0-9_]{0,63}$'::text)),
    CONSTRAINT audit_ownership_shape CHECK (((((ownership_scope)::text = 'parish'::text) AND (parish_id IS NOT NULL)) OR ((campaign_reference IS NULL) AND ((ownership_scope)::text = 'deployment'::text) AND (parish_id IS NULL))))
);

-- TABLE: stewardship_auth_incident
CREATE TABLE public.stewardship_auth_incident (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    kind character varying(32) NOT NULL,
    "window" bigint NOT NULL,
    level character varying(16) NOT NULL,
    attempts integer NOT NULL,
    sources integer NOT NULL,
    identities integer NOT NULL,
    candidates integer NOT NULL,
    resolved_at timestamp with time zone,
    notification_pending boolean NOT NULL,
    CONSTRAINT auth_incident_kind CHECK (((kind)::text = ANY ((ARRAY['limiter_unavailable'::character varying, 'limiter_state_lost'::character varying, 'admin_abuse'::character varying, 'family_abuse'::character varying])::text[]))),
    CONSTRAINT auth_incident_level CHECK (((level)::text = ANY ((ARRAY['WARNING'::character varying, 'CRITICAL'::character varying])::text[]))),
    CONSTRAINT stewardship_accounts_authenticationincident_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_auth_incident_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT stewardship_auth_incident_candidates_check CHECK ((candidates >= 0)),
    CONSTRAINT stewardship_auth_incident_identities_check CHECK ((identities >= 0)),
    CONSTRAINT stewardship_auth_incident_sources_check CHECK ((sources >= 0)),
    CONSTRAINT stewardship_auth_incident_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_auth_incident_window_check CHECK (("window" >= 0))
);

-- TABLE: stewardship_branding_asset
CREATE TABLE public.stewardship_branding_asset (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    label character varying(8) NOT NULL,
    width integer NOT NULL,
    height integer NOT NULL,
    size integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    bundle_id uuid NOT NULL,
    CONSTRAINT branding_asset_bounds CHECK (((height >= 1) AND (height <= 1024) AND (size >= 1) AND (size <= 5242880) AND (width >= 1) AND (width <= 1024))),
    CONSTRAINT branding_asset_digest CHECK (((sha256)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT branding_asset_label CHECK (((label)::text = ANY ((ARRAY['large'::character varying, 'menu'::character varying, 'icon'::character varying, 'favicon'::character varying])::text[]))),
    CONSTRAINT stewardship_branding_asset_height_check CHECK ((height >= 0)),
    CONSTRAINT stewardship_branding_asset_size_check CHECK ((size >= 0)),
    CONSTRAINT stewardship_branding_asset_width_check CHECK ((width >= 0))
);

-- TABLE: stewardship_branding_bundle
CREATE TABLE public.stewardship_branding_bundle (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    owner_id uuid NOT NULL,
    session_id uuid NOT NULL,
    setup_attempt_id uuid,
    expires_at timestamp with time zone NOT NULL,
    state character varying(16) DEFAULT 'writing'::character varying NOT NULL,
    base_id uuid NOT NULL,
    CONSTRAINT branding_bundle_interval CHECK ((expires_at > created_at)),
    CONSTRAINT branding_bundle_state CHECK (((state)::text = ANY ((ARRAY['writing'::character varying, 'ready'::character varying, 'cleanup_pending'::character varying, 'scrubbed'::character varying])::text[]))),
    CONSTRAINT stewardship_accounts_brandingbundle_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_branding_bundle_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_campaign
CREATE TABLE public.stewardship_campaign (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    state character varying(24) NOT NULL,
    active_configuration_id uuid NOT NULL,
    active_token_generation_id uuid,
    delivery_paused boolean DEFAULT false NOT NULL,
    ever_active boolean DEFAULT false NOT NULL,
    first_live_delivery_at timestamp with time zone,
    first_live_submission_at timestamp with time zone,
    pause_actor_id uuid,
    pause_reason character varying(1024) DEFAULT ''::character varying NOT NULL,
    pause_version bigint DEFAULT 0 NOT NULL,
    paused_at timestamp with time zone,
    readiness_revision bigint DEFAULT 0 NOT NULL,
    resumed_at timestamp with time zone,
    structural_locked boolean DEFAULT false NOT NULL,
    CONSTRAINT campaign_known_state CHECK (((state)::text = ANY ((ARRAY['draft'::character varying, 'scheduled'::character varying, 'active'::character varying, 'closed'::character varying, 'archived'::character varying, 'purging'::character varying, 'purge_cleanup_failed'::character varying, 'purged'::character varying])::text[]))),
    CONSTRAINT campaign_live_structure_locked CHECK (((NOT ever_active) OR structural_locked)),
    CONSTRAINT campaign_pause_evidence CHECK (((NOT delivery_paused) OR ((pause_actor_id IS NOT NULL) AND (pause_version > 0) AND (paused_at IS NOT NULL)))),
    CONSTRAINT stewardship_campaign_pause_version_check CHECK ((pause_version >= 0)),
    CONSTRAINT stewardship_campaign_readiness_revision_check CHECK ((readiness_revision >= 0)),
    CONSTRAINT stewardship_campaign_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_campaigns_campaign_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_campaign_boundary
CREATE TABLE public.stewardship_campaign_boundary (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    kind character varying(8) NOT NULL,
    task_fence bigint,
    due_at timestamp with time zone NOT NULL,
    state character varying(16) NOT NULL,
    completed_at timestamp with time zone,
    reason character varying(64) NOT NULL,
    campaign_id uuid NOT NULL,
    task_id uuid,
    transition_id uuid,
    execution_revision bigint NOT NULL,
    CONSTRAINT campaign_boundary_execution_revision CHECK (execution_revision >= 1),
    CONSTRAINT stewardship_campaign_boundary_execution_revision_check CHECK (execution_revision >= 0),
    CONSTRAINT campaign_boundary_kind CHECK (((kind)::text = ANY ((ARRAY['start'::character varying, 'close'::character varying])::text[]))),
    CONSTRAINT campaign_boundary_result CHECK ((((completed_at IS NULL) AND ((state)::text = 'pending'::text) AND (transition_id IS NULL)) OR ((completed_at IS NOT NULL) AND ((state)::text = 'succeeded'::text) AND (transition_id IS NOT NULL)) OR ((completed_at IS NOT NULL) AND ((state)::text = 'skipped'::text) AND (transition_id IS NULL)))),
    CONSTRAINT campaign_boundary_state CHECK (((state)::text = ANY ((ARRAY['pending'::character varying, 'succeeded'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT stewardship_campaign_boundary_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_campaign_boundary_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_campaigns_campaignboundaryoccurrence_positive_versi CHECK ((version >= 1))
);

-- TABLE: stewardship_campaign_config_abort
CREATE TABLE public.stewardship_campaign_config_abort (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    reason character varying(1024) NOT NULL,
    intent_id uuid NOT NULL
);

-- TABLE: stewardship_campaign_config_intent
CREATE TABLE public.stewardship_campaign_config_intent (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    action character varying(16) NOT NULL,
    expected_version bigint NOT NULL,
    expected_runtime_version bigint CONSTRAINT stewardship_campaign_config_i_expected_runtime_version_not_null NOT NULL,
    token_generation_id uuid,
    campaign_id uuid NOT NULL,
    prior_projection_id uuid NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT stewardship_campaign_config_inte_expected_runtime_version_check CHECK ((expected_runtime_version >= 0)),
    CONSTRAINT stewardship_campaign_config_intent_expected_version_check CHECK ((expected_version >= 0))
);

-- TABLE: stewardship_campaign_configuration
CREATE TABLE public.stewardship_campaign_configuration (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    name character varying(254) NOT NULL,
    timezone character varying(254) NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    ends_at timestamp with time zone NOT NULL,
    "values" jsonb NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT campaign_ordered_dates CHECK ((end_date > start_date)),
    CONSTRAINT campaign_ordered_instants CHECK ((ends_at > starts_at))
);

-- TABLE: stewardship_campaign_control
CREATE TABLE public.stewardship_campaign_control (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    request_id uuid NOT NULL,
    expected_version bigint NOT NULL,
    expected_runtime_version bigint NOT NULL,
    action character varying(24) NOT NULL,
    reason character varying(1024) NOT NULL,
    evidence_id uuid,
    occurred_at timestamp with time zone NOT NULL,
    campaign_id uuid NOT NULL,
    CONSTRAINT stewardship_campaign_control_expected_runtime_version_check CHECK ((expected_runtime_version >= 0)),
    CONSTRAINT stewardship_campaign_control_expected_version_check CHECK ((expected_version >= 0))
);

-- TABLE: stewardship_campaign_credentials
CREATE TABLE public.stewardship_campaign_credentials (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    go_live_gate boolean NOT NULL,
    campaign_id uuid NOT NULL,
    rehearsal_epoch_id uuid,
    eligibility_digest character varying(64) NOT NULL,
    eligible_count bigint NOT NULL,
    source_generation bigint,
    source_snapshot_id uuid,
    population_dirty boolean NOT NULL,
    CONSTRAINT stewardship_campaign_credentials_eligible_count_check CHECK ((eligible_count >= 0)),
    CONSTRAINT stewardship_campaign_credentials_source_generation_check CHECK ((source_generation >= 0)),
    CONSTRAINT stewardship_campaign_credentials_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_campaigns_campaigncredentialstate_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_campaign_mail_test
CREATE TABLE public.stewardship_campaign_mail_test (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    requested_by_id uuid NOT NULL,
    request_key uuid NOT NULL,
    fingerprint character varying(64) NOT NULL,
    mail jsonb NOT NULL,
    state character varying(24) NOT NULL,
    task_fence bigint,
    worker_id uuid,
    submitted_at timestamp with time zone,
    deadline_at timestamp with time zone,
    finished_at timestamp with time zone,
    campaign_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    run_id uuid,
    task_id uuid NOT NULL,
    template_id uuid NOT NULL,
    CONSTRAINT campaign_mail_fingerprint CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT campaign_mail_known_state CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying, 'accepted'::character varying, 'not_sent'::character varying, 'delivery_unknown'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT campaign_mail_submission_shape CHECK ((((deadline_at IS NULL) AND (run_id IS NULL) AND ((state)::text = ANY ((ARRAY['queued'::character varying, 'cancelled'::character varying])::text[])) AND (submitted_at IS NULL) AND (task_fence IS NULL) AND (worker_id IS NULL)) OR ((deadline_at IS NOT NULL) AND (run_id IS NOT NULL) AND ((state)::text = ANY ((ARRAY['submitting'::character varying, 'accepted'::character varying, 'not_sent'::character varying, 'delivery_unknown'::character varying])::text[])) AND (submitted_at IS NOT NULL) AND (task_fence >= 1) AND (worker_id IS NOT NULL)))),
    CONSTRAINT campaign_mail_terminal_scrub CHECK ((((finished_at IS NULL) AND ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]))) OR ((finished_at IS NOT NULL) AND (mail = '{}'::jsonb) AND (NOT ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[])))))),
    CONSTRAINT stewardship_accounts_campaignmailtest_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_campaign_mail_test_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_campaign_mail_test_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_campaign_transition
CREATE TABLE public.stewardship_campaign_transition (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    request_id uuid NOT NULL,
    action character varying(24) NOT NULL,
    expected_version bigint NOT NULL,
    expected_runtime_version bigint CONSTRAINT stewardship_campaign_transiti_expected_runtime_version_not_null NOT NULL,
    before_state character varying(24) NOT NULL,
    after_state character varying(24) NOT NULL,
    before_mode character varying(16) NOT NULL,
    after_mode character varying(16) NOT NULL,
    boundary_id uuid,
    task_fence bigint,
    token_generation_id uuid,
    reason character varying(1024) NOT NULL,
    campaign_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    prior_projection_id uuid,
    CONSTRAINT stewardship_campaign_transition_expected_runtime_version_check CHECK ((expected_runtime_version >= 0)),
    CONSTRAINT stewardship_campaign_transition_expected_version_check CHECK ((expected_version >= 0)),
    CONSTRAINT stewardship_campaign_transition_task_fence_check CHECK ((task_fence >= 0))
);

-- TABLE: stewardship_campaign_work_gate
CREATE TABLE public.stewardship_campaign_work_gate (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    request_id uuid NOT NULL,
    initiated_by_id uuid,
    state character varying(16) NOT NULL,
    campaign_id uuid NOT NULL,
    CONSTRAINT campaign_work_gate_state CHECK (((state)::text = ANY ((ARRAY['preparing'::character varying, 'running'::character varying, 'released'::character varying, 'tombstone'::character varying])::text[]))),
    CONSTRAINT stewardship_campaign_work_gate_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_campaigns_campaignworkgate_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_catchup_checkpoint
CREATE TABLE public.stewardship_catchup_checkpoint (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    sequence bigint NOT NULL,
    group_key character varying(128) NOT NULL,
    cursor character varying(128) NOT NULL,
    items bigint NOT NULL,
    fence bigint NOT NULL,
    demand_id uuid NOT NULL,
    task_id uuid NOT NULL,
    complete boolean NOT NULL,
    phase character varying(32) NOT NULL,
    CONSTRAINT stewardship_catchup_checkpoint_fence_check CHECK ((fence >= 0)),
    CONSTRAINT stewardship_catchup_checkpoint_items_check CHECK ((items >= 0)),
    CONSTRAINT stewardship_catchup_checkpoint_sequence_check CHECK ((sequence >= 0))
);

-- TABLE: stewardship_catchup_failure
CREATE TABLE public.stewardship_catchup_failure (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    expected_version bigint NOT NULL,
    fence bigint NOT NULL,
    code character varying(32) NOT NULL,
    demand_id uuid NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT stewardship_catchup_failure_expected_version_check CHECK ((expected_version >= 0)),
    CONSTRAINT stewardship_catchup_failure_fence_check CHECK ((fence >= 0))
);

-- TABLE: stewardship_chair_reconciliation
CREATE TABLE public.stewardship_chair_reconciliation (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_fence bigint,
    decisions jsonb NOT NULL,
    owner_transaction bigint DEFAULT txid_current() NOT NULL,
    activation_id uuid,
    configuration_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    source_owner_id uuid,
    CONSTRAINT chair_reconciliation_owner CHECK ((((activation_id IS NOT NULL) AND (source_fence IS NULL) AND (source_owner_id IS NULL)) OR ((activation_id IS NULL) AND (source_fence > 0) AND (source_fence IS NOT NULL) AND (source_owner_id IS NOT NULL)))),
    CONSTRAINT stewardship_chair_reconciliation_owner_transaction_check CHECK ((owner_transaction >= 0)),
    CONSTRAINT stewardship_chair_reconciliation_source_fence_check CHECK ((source_fence >= 0))
);

-- TABLE: stewardship_chair_review
CREATE TABLE public.stewardship_chair_review (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    assignment_record_id uuid NOT NULL,
    close_reason character varying(24) NOT NULL,
    closed_by_id uuid,
    latest_by_id uuid NOT NULL,
    opened_by_id uuid NOT NULL,
    CONSTRAINT chair_review_closure CHECK (((((close_reason)::text = ''::text) AND (closed_by_id IS NULL)) OR (((close_reason)::text = ANY ((ARRAY['relationship_returned'::character varying, 'assignment_removed'::character varying])::text[])) AND (closed_by_id IS NOT NULL)))),
    CONSTRAINT stewardship_accounts_chairassignmentreview_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_chair_review_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_chair_seed_evidence
CREATE TABLE public.stewardship_chair_seed_evidence (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    assignment_record_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    member_duid bigint NOT NULL,
    roster_keys jsonb NOT NULL,
    assignment_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    CONSTRAINT chair_seed_evidence_identity CHECK (((actor_id IS NOT NULL) AND (member_duid > 0) AND (member_duid < '2147483648'::bigint) AND (organization_id > 0) AND (organization_id < '2147483648'::bigint))),
    CONSTRAINT stewardship_chair_seed_evidence_member_duid_check CHECK ((member_duid >= 0)),
    CONSTRAINT stewardship_chair_seed_evidence_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_config_activation
CREATE TABLE public.stewardship_config_activation (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    sequence bigint NOT NULL,
    configuration_id uuid NOT NULL,
    predecessor_id uuid,
    request_id uuid,
    CONSTRAINT activation_bootstrap_or_request CHECK ((((predecessor_id IS NULL) AND (request_id IS NULL) AND (sequence = 1)) OR ((predecessor_id IS NOT NULL) AND (request_id IS NOT NULL) AND (sequence > 1)))),
    CONSTRAINT activation_positive_sequence CHECK ((sequence >= 1)),
    CONSTRAINT stewardship_config_activation_sequence_check CHECK ((sequence >= 0))
);

-- TABLE: stewardship_config_checkpoint
CREATE TABLE public.stewardship_config_checkpoint (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    sequence integer NOT NULL,
    state character varying(16) NOT NULL,
    request_id uuid NOT NULL,
    failure_code character varying(32) DEFAULT ''::character varying NOT NULL,
    CONSTRAINT config_checkpoint_failure_code CHECK (((((failure_code)::text = ANY ((ARRAY['stale_base'::character varying, 'invalid_candidate'::character varying])::text[])) AND ((state)::text = 'failed'::text)) OR ((NOT ((state)::text = 'failed'::text)) AND ((failure_code)::text = ''::text)))),
    CONSTRAINT config_checkpoint_installer_states CHECK (((state)::text = ANY ((ARRAY['staged'::character varying, 'validating'::character varying, 'prepared'::character varying, 'yaml_activated'::character varying, 'applied'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT config_checkpoint_positive CHECK ((sequence >= 1)),
    CONSTRAINT stewardship_config_checkpoint_sequence_check CHECK ((sequence >= 0))
);

-- TABLE: stewardship_config_request
CREATE TABLE public.stewardship_config_request (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    request_key uuid NOT NULL,
    request_schema character varying(64) NOT NULL,
    patch jsonb NOT NULL,
    payload_fingerprint character varying(64) NOT NULL,
    candidate_version_id uuid NOT NULL,
    candidate_digest character varying(64) NOT NULL,
    base_id uuid NOT NULL,
    authority character varying(24) DEFAULT 'admin'::character varying NOT NULL,
    confirmed_deployment_id uuid,
    operator_name character varying(254),
    operator_reason character varying(1024),
    recovery_target character varying(254),
    CONSTRAINT config_request_actor CHECK ((((actor_id IS NOT NULL) AND ((authority)::text = 'admin'::text) AND (confirmed_deployment_id IS NULL) AND (operator_name IS NULL) AND (operator_reason IS NULL) AND (recovery_target IS NULL) AND (NOT ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying])::text[])))) OR ((actor_id IS NULL) AND ((authority)::text = 'operator_recovery'::text) AND (confirmed_deployment_id IS NOT NULL) AND (operator_name IS NOT NULL) AND (operator_reason IS NOT NULL) AND (recovery_target IS NOT NULL) AND ((request_schema)::text = ANY ((ARRAY['operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'operator-recovery-ministry-v4'::character varying, 'operator-recovery-content-v5'::character varying, 'operator-recovery-cadence-v8'::character varying])::text[]))))),
    CONSTRAINT config_request_candidate_digest CHECK (((candidate_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT config_request_payload_digest CHECK (((payload_fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT config_request_schema CHECK (((request_schema)::text = ANY ((ARRAY['parish-integrations-patch-v1'::character varying, 'foundation-policy-patch-v2'::character varying, 'operator-recovery-patch-v1'::character varying, 'operator-recovery-patch-v2'::character varying, 'operator-recovery-bootstrap-v1'::character varying, 'campaign-foundation-patch-v3'::character varying, 'ministry-activity-patch-v4'::character varying, 'operator-recovery-ministry-v4'::character varying, 'campaign-content-patch-v5'::character varying, 'operator-recovery-content-v5'::character varying, 'integration-credential-patch-v6'::character varying, 'initial-setup-patch-v7'::character varying, 'source-cadence-patch-v8'::character varying, 'operator-recovery-cadence-v8'::character varying, 'integration-credential-cadence-v8'::character varying])::text[])))
);

-- TABLE: stewardship_configuration_version
CREATE TABLE public.stewardship_configuration_version (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    digest character varying(64) NOT NULL,
    schema_version smallint NOT NULL,
    canonical_document jsonb NOT NULL,
    normalized_digest character varying(64) NOT NULL,
    validation_schema character varying(64) NOT NULL,
    predecessor_id uuid,
    CONSTRAINT configuration_not_own_predecessor CHECK ((NOT ((predecessor_id = id) AND (predecessor_id IS NOT NULL)))),
    CONSTRAINT configuration_schema_v1 CHECK ((schema_version = 1)),
    CONSTRAINT configuration_valid_digests CHECK ((((digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((normalized_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT configuration_validation_schema CHECK (((validation_schema)::text = ANY ((ARRAY['parish-integrations-v1'::character varying, 'foundation-policy-v2'::character varying, 'campaign-foundation-v3'::character varying, 'bootstrap-policy-v1'::character varying, 'ministry-activity-v4'::character varying, 'campaign-content-v5'::character varying, 'source-cadence-v8'::character varying])::text[]))),
    CONSTRAINT stewardship_configuration_version_schema_version_check CHECK ((schema_version >= 0))
);

-- TABLE: stewardship_content_version
CREATE TABLE public.stewardship_content_version (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    kind character varying(8) NOT NULL,
    slot character varying(32) NOT NULL,
    subject character varying(254),
    html text NOT NULL,
    text text NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT content_subject_kind CHECK (((((kind)::text = 'page'::text) AND (subject IS NULL)) OR (((kind)::text = 'email'::text) AND (subject IS NOT NULL) AND (NOT (((subject)::text = ''::text) AND (subject IS NOT NULL))))))
);

-- TABLE: stewardship_credential_consumer_ack
CREATE TABLE public.stewardship_credential_consumer_ack (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    consumer character varying(32) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT credential_ack_fingerprint CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text))
);

ALTER TABLE ONLY public.stewardship_credential_consumer_ack FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_credential_deployment
CREATE TABLE public.stewardship_credential_deployment (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    family_link_epoch uuid NOT NULL,
    restore_id uuid,
    CONSTRAINT stewardship_campaigns_deploymentcredentialstate_positive_versio CHECK ((version >= 1)),
    CONSTRAINT stewardship_credential_deployment_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_credential_key_state
CREATE TABLE public.stewardship_credential_key_state (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    kind character varying(32) NOT NULL,
    inventory_digest character varying(64) NOT NULL,
    inventory jsonb NOT NULL,
    CONSTRAINT stewardship_campaigns_credentialkeystate_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_credential_key_state_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_snapshot_contact
CREATE TABLE public.stewardship_snapshot_contact (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_member
CREATE TABLE public.stewardship_snapshot_member (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_ministry
CREATE TABLE public.stewardship_snapshot_ministry (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_roster
CREATE TABLE public.stewardship_snapshot_roster (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_source_contact
CREATE TABLE public.stewardship_source_contact (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    owner_kind character varying(8) NOT NULL,
    owner_key character varying(200) NOT NULL,
    CONSTRAINT source_contact_owner_kind CHECK (((owner_kind)::text = ANY ((ARRAY['family'::character varying, 'member'::character varying])::text[]))),
    CONSTRAINT sourcecontact_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_contact_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_current
CREATE TABLE public.stewardship_source_current (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    singleton boolean NOT NULL,
    generation bigint NOT NULL,
    organization_id bigint,
    snapshot_id uuid,
    CONSTRAINT source_current_shape CHECK ((((generation = 0) AND (organization_id IS NULL) AND (snapshot_id IS NULL)) OR ((generation > 0) AND (organization_id > 0) AND (organization_id IS NOT NULL) AND (snapshot_id IS NOT NULL)))),
    CONSTRAINT source_current_singleton CHECK (singleton),
    CONSTRAINT stewardship_source_current_generation_check CHECK ((generation >= 0)),
    CONSTRAINT stewardship_source_current_organization_id_check CHECK ((organization_id >= 0)),
    CONSTRAINT stewardship_source_current_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_source_sourcecurrent_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_source_member
CREATE TABLE public.stewardship_source_member (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    family_key character varying(200) NOT NULL,
    CONSTRAINT sourcemember_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_member_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_ministry
CREATE TABLE public.stewardship_source_ministry (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    CONSTRAINT sourceministry_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_ministry_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_roster
CREATE TABLE public.stewardship_source_roster (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    member_key character varying(200) NOT NULL,
    ministry_key character varying(200) NOT NULL,
    CONSTRAINT sourceroster_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_roster_organization_id_check CHECK ((organization_id >= 0))
);

-- VIEW: stewardship_current_chair
CREATE VIEW public.stewardship_current_chair WITH (security_barrier='true') AS
 SELECT cur.snapshot_id,
    cur.generation,
    cur.organization_id,
    (member.source_key)::bigint AS member_duid,
    (ministry.source_key)::bigint AS ministry_duid,
    (email.value ->> 'value'::text) AS email,
    (((contact.canonical)::jsonb ->> 'publish_email'::text))::boolean AS publish_email,
    roster.source_key AS roster_key
   FROM (((((((((public.stewardship_source_current cur
     JOIN public.stewardship_snapshot_roster rm ON ((rm.snapshot_id = cur.snapshot_id)))
     JOIN public.stewardship_source_roster roster ON ((roster.id = rm.payload_id)))
     JOIN public.stewardship_snapshot_member mm ON (((mm.snapshot_id = cur.snapshot_id) AND ((mm.source_key)::text = (roster.member_key)::text))))
     JOIN public.stewardship_source_member member ON ((member.id = mm.payload_id)))
     JOIN public.stewardship_snapshot_ministry tm ON (((tm.snapshot_id = cur.snapshot_id) AND ((tm.source_key)::text = (roster.ministry_key)::text))))
     JOIN public.stewardship_source_ministry ministry ON ((ministry.id = tm.payload_id)))
     JOIN public.stewardship_snapshot_contact cm ON (((cm.snapshot_id = cur.snapshot_id) AND ((cm.source_key)::text = ('member:'::text || (member.source_key)::text)))))
     JOIN public.stewardship_source_contact contact ON ((contact.id = cm.payload_id)))
     CROSS JOIN LATERAL jsonb_array_elements(((contact.canonical)::jsonb -> 'emails'::text)) email(value))
  WHERE ((((member.canonical)::jsonb -> 'schema_version'::text) = '1'::jsonb) AND (((contact.canonical)::jsonb -> 'schema_version'::text) = '1'::jsonb) AND (((ministry.canonical)::jsonb -> 'schema_version'::text) = '1'::jsonb) AND (((roster.canonical)::jsonb -> 'schema_version'::text) = '1'::jsonb) AND (((member.canonical)::jsonb -> 'active'::text) = 'true'::jsonb) AND (((ministry.canonical)::jsonb -> 'catalog_present'::text) = 'true'::jsonb) AND (((roster.canonical)::jsonb -> 'current'::text) = 'true'::jsonb) AND (translate(((roster.canonical)::jsonb ->> 'ministryRoleName'::text), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'::text, 'abcdefghijklmnopqrstuvwxyz'::text) = 'chairperson'::text) AND ((email.value -> 'valid'::text) = 'true'::jsonb));

-- TABLE: stewardship_daily_fact
CREATE TABLE public.stewardship_daily_fact (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    local_date date NOT NULL,
    first_responses bigint NOT NULL,
    cumulative_responses bigint NOT NULL,
    cohort_denominator bigint NOT NULL,
    source_generation bigint,
    source_as_of timestamp with time zone,
    population_available boolean NOT NULL,
    pledge_available boolean NOT NULL,
    pledge_total numeric(18,2),
    fact_set_id uuid NOT NULL,
    CONSTRAINT daily_fact_count_bounds CHECK (((cumulative_responses <= cohort_denominator) AND (first_responses <= cumulative_responses))),
    CONSTRAINT daily_fact_pledge_availability CHECK ((((NOT pledge_available) AND (pledge_total IS NULL)) OR (pledge_available AND (pledge_total >= (0)::numeric) AND (pledge_total IS NOT NULL) AND (pledge_total <= 9999999999999999.99) AND population_available))),
    CONSTRAINT daily_fact_source_availability CHECK ((((cohort_denominator = 0) AND (cumulative_responses = 0) AND (first_responses = 0) AND (NOT population_available) AND (source_as_of IS NULL) AND (source_generation IS NULL)) OR (population_available AND (source_as_of IS NOT NULL) AND (source_generation > 0) AND (source_generation IS NOT NULL)))),
    CONSTRAINT stewardship_daily_fact_cohort_denominator_check CHECK ((cohort_denominator >= 0)),
    CONSTRAINT stewardship_daily_fact_cumulative_responses_check CHECK ((cumulative_responses >= 0)),
    CONSTRAINT stewardship_daily_fact_first_responses_check CHECK ((first_responses >= 0)),
    CONSTRAINT stewardship_daily_fact_source_generation_check CHECK ((source_generation >= 0))
);

-- TABLE: stewardship_daily_fact_set
CREATE TABLE public.stewardship_daily_fact_set (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    population_scope character varying(12) NOT NULL,
    source_generation bigint NOT NULL,
    submission_watermark bigint NOT NULL,
    through_date date NOT NULL,
    first_date date,
    last_date date,
    expected_count integer NOT NULL,
    state character varying(8) NOT NULL,
    task_fence bigint NOT NULL,
    worker_id uuid NOT NULL,
    ready_at timestamp with time zone,
    failure_code character varying(48) NOT NULL,
    campaign_id uuid NOT NULL,
    source_id uuid NOT NULL,
    task_id uuid NOT NULL,
    timezone_configuration_id uuid NOT NULL,
    CONSTRAINT daily_fact_expected_dates CHECK ((((expected_count = 0) AND (first_date IS NULL) AND (last_date IS NULL)) OR ((expected_count > 0) AND (first_date IS NOT NULL) AND (last_date >= first_date) AND (last_date IS NOT NULL)))),
    CONSTRAINT daily_fact_failure_code CHECK ((((failure_code)::text = ''::text) OR ((failure_code)::text ~ '^[a-z][a-z0-9_]{0,47}$'::text))),
    CONSTRAINT daily_fact_positive_fences CHECK (((source_generation > 0) AND (task_fence > 0))),
    CONSTRAINT daily_fact_ready_instant CHECK ((((ready_at IS NOT NULL) AND ((state)::text = 'ready'::text)) OR ((NOT ((state)::text = 'ready'::text)) AND (ready_at IS NULL)))),
    CONSTRAINT daily_fact_scope CHECK (((population_scope)::text = ANY ((ARRAY['historical'::character varying, 'current'::character varying])::text[]))),
    CONSTRAINT daily_fact_state CHECK (((state)::text = ANY ((ARRAY['building'::character varying, 'ready'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT stewardship_daily_fact_set_expected_count_check CHECK ((expected_count >= 0)),
    CONSTRAINT stewardship_daily_fact_set_source_generation_check CHECK ((source_generation >= 0)),
    CONSTRAINT stewardship_daily_fact_set_submission_watermark_check CHECK ((submission_watermark >= 0)),
    CONSTRAINT stewardship_daily_fact_set_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_daily_fact_set_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_reports_campaigndailyfactset_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_domain_rule
CREATE TABLE public.stewardship_domain_rule (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    domain character varying(253) NOT NULL,
    roles jsonb NOT NULL,
    configuration_id uuid NOT NULL
);

-- TABLE: stewardship_download_policy
CREATE TABLE public.stewardship_download_policy (
    id integer NOT NULL,
    capacity integer NOT NULL,
    version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    CONSTRAINT stewardship_download_policy_capacity_check CHECK (((capacity >= 1) AND (capacity <= 32))),
    CONSTRAINT stewardship_download_policy_id_check CHECK ((id = 1)),
    CONSTRAINT stewardship_download_policy_version_check CHECK ((version > 0))
);

-- TABLE: stewardship_fact_compaction
CREATE TABLE public.stewardship_fact_compaction (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    fact_set_id uuid NOT NULL,
    population_scope character varying(12) NOT NULL,
    source_generation bigint NOT NULL,
    submission_watermark bigint NOT NULL,
    timezone_configuration_id uuid NOT NULL,
    through_date date NOT NULL,
    row_count integer NOT NULL,
    task_fence bigint NOT NULL,
    worker_id uuid NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT stewardship_fact_compaction_row_count_check CHECK ((row_count >= 0)),
    CONSTRAINT stewardship_fact_compaction_source_generation_check CHECK ((source_generation >= 0)),
    CONSTRAINT stewardship_fact_compaction_submission_watermark_check CHECK ((submission_watermark >= 0)),
    CONSTRAINT stewardship_fact_compaction_task_fence_check CHECK ((task_fence >= 0))
);

-- TABLE: stewardship_fact_demand
CREATE TABLE public.stewardship_fact_demand (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    population_scope character varying(12) NOT NULL,
    requested_source_generation bigint NOT NULL,
    requested_submission_watermark bigint NOT NULL,
    requested_through_date date NOT NULL,
    pending_revision bigint NOT NULL,
    pending_first_at timestamp with time zone,
    pending_last_at timestamp with time zone,
    pending_due_at timestamp with time zone,
    claimed_revision bigint NOT NULL,
    campaign_id uuid NOT NULL,
    claimed_generation_id uuid,
    claimed_task_id uuid,
    requested_source_id uuid NOT NULL,
    requested_timezone_configuration_id uuid CONSTRAINT stewardship_fact_demand_requested_timezone_configurati_not_null NOT NULL,
    claimed_task_fence bigint,
    claimed_worker_id uuid,
    CONSTRAINT fact_demand_claim_shape CHECK ((((claimed_generation_id IS NULL) AND (claimed_task_id IS NULL) AND (claimed_task_fence IS NULL) AND (claimed_worker_id IS NULL)) OR ((claimed_generation_id IS NOT NULL) AND (claimed_revision > 0) AND (claimed_task_id IS NOT NULL) AND (claimed_task_fence > 0) AND (claimed_task_fence IS NOT NULL) AND (claimed_worker_id IS NOT NULL)))),
    CONSTRAINT fact_demand_known_scope CHECK (((population_scope)::text = ANY ((ARRAY['historical'::character varying, 'current'::character varying])::text[]))),
    CONSTRAINT fact_demand_revision_order CHECK (((pending_revision >= claimed_revision) AND (requested_source_generation > 0))),
    CONSTRAINT fact_demand_window_shape CHECK ((((pending_due_at IS NULL) AND (pending_first_at IS NULL) AND (pending_last_at IS NULL)) OR ((pending_due_at >= pending_first_at) AND (pending_due_at IS NOT NULL) AND (pending_first_at IS NOT NULL) AND (pending_last_at >= pending_first_at) AND (pending_last_at IS NOT NULL)))),
    CONSTRAINT stewardship_fact_demand_claimed_revision_check CHECK ((claimed_revision >= 0)),
    CONSTRAINT stewardship_fact_demand_claimed_task_fence_check CHECK ((claimed_task_fence >= 0)),
    CONSTRAINT stewardship_fact_demand_pending_revision_check CHECK ((pending_revision >= 0)),
    CONSTRAINT stewardship_fact_demand_requested_source_generation_check CHECK ((requested_source_generation >= 0)),
    CONSTRAINT stewardship_fact_demand_requested_submission_watermark_check CHECK ((requested_submission_watermark >= 0)),
    CONSTRAINT stewardship_fact_demand_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_reports_campaignfactrebuilddemand_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_fact_pin
CREATE TABLE public.stewardship_fact_pin (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    parent_kind character varying(16) NOT NULL,
    parent_id uuid NOT NULL,
    fact_set_id uuid NOT NULL,
    CONSTRAINT fact_pin_kind CHECK (((parent_kind)::text = ANY ((ARRAY['export'::character varying, 'digest'::character varying, 'render'::character varying, 'verification'::character varying, 'work'::character varying, 'operator'::character varying])::text[]))),
    CONSTRAINT stewardship_fact_pin_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_reports_campaignfactpin_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_fact_pointer
CREATE TABLE public.stewardship_fact_pointer (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    population_scope character varying(12) NOT NULL,
    campaign_id uuid NOT NULL,
    fact_set_id uuid NOT NULL,
    CONSTRAINT fact_pointer_known_scope CHECK (((population_scope)::text = ANY ((ARRAY['historical'::character varying, 'current'::character varying])::text[]))),
    CONSTRAINT stewardship_fact_pointer_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_reports_campaignfactpointer_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_family_campaign
CREATE TABLE public.stewardship_family_campaign (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    family_duid bigint NOT NULL,
    active boolean NOT NULL,
    portal_eligible boolean NOT NULL,
    email_eligible boolean NOT NULL,
    email_deliverable boolean NOT NULL,
    status_reason character varying(48) NOT NULL,
    deliverability_reason character varying(48) NOT NULL,
    first_eligible_at timestamp with time zone,
    first_eligible_source_generation bigint,
    eligibility_changed_at timestamp with time zone NOT NULL,
    source_generation bigint NOT NULL,
    code_ciphertext text,
    initial_invitation_state character varying(24) NOT NULL,
    first_live_submission_id uuid,
    effective_submission_id uuid,
    last_activity_at timestamp with time zone,
    campaign_id uuid NOT NULL,
    CONSTRAINT family_campaign_positive_duid CHECK ((family_duid > 0)),
    CONSTRAINT family_cohort_pair CHECK ((((first_eligible_at IS NULL) AND (first_eligible_source_generation IS NULL)) OR ((first_eligible_at IS NOT NULL) AND (first_eligible_source_generation IS NOT NULL)))),
    CONSTRAINT family_deliverable_is_eligible CHECK (((NOT email_deliverable) OR email_eligible)),
    CONSTRAINT family_eligible_has_code_cohort CHECK (((NOT portal_eligible) OR (active AND (code_ciphertext IS NOT NULL) AND (first_eligible_at IS NOT NULL) AND (first_eligible_source_generation IS NOT NULL)))),
    CONSTRAINT stewardship_campaigns_familycampaign_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_family_campaign_family_duid_check CHECK ((family_duid >= 0)),
    CONSTRAINT stewardship_family_campaign_first_eligible_source_generat_check CHECK ((first_eligible_source_generation >= 0)),
    CONSTRAINT stewardship_family_campaign_source_generation_check CHECK ((source_generation >= 0)),
    CONSTRAINT stewardship_family_campaign_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_family_code_mac
CREATE TABLE public.stewardship_family_code_mac (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    key_id character varying(48) NOT NULL,
    algorithm character varying(24) NOT NULL,
    digest character varying(64) NOT NULL,
    campaign_id uuid NOT NULL,
    family_id uuid NOT NULL,
    CONSTRAINT family_code_mac_format CHECK ((((algorithm)::text = 'hmac-sha256-v1'::text) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: stewardship_family_eligibility
CREATE TABLE public.stewardship_family_eligibility (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_generation bigint NOT NULL,
    family_version bigint NOT NULL,
    active boolean NOT NULL,
    portal_eligible boolean NOT NULL,
    email_eligible boolean NOT NULL,
    email_deliverable boolean NOT NULL,
    status_reason character varying(48) NOT NULL,
    deliverability_reason character varying(48) NOT NULL,
    family_id uuid NOT NULL,
    CONSTRAINT stewardship_family_eligibility_family_version_check CHECK ((family_version >= 0)),
    CONSTRAINT stewardship_family_eligibility_source_generation_check CHECK ((source_generation >= 0))
);

-- TABLE: stewardship_family_session
CREATE TABLE public.stewardship_family_session (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    mode character varying(16) NOT NULL,
    authenticated_at timestamp with time zone NOT NULL,
    last_activity_at timestamp with time zone NOT NULL,
    last_keepalive_at timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    family_id uuid NOT NULL,
    session_id character varying(40) NOT NULL,
    rehearsal_epoch_id uuid,
    credential_epoch uuid NOT NULL,
    presence_at timestamp with time zone,
    presence_section character varying(24) NOT NULL,
    CONSTRAINT family_presence_shape CHECK ((((presence_at IS NULL) AND ((presence_section)::text = ''::text)) OR ((presence_at >= authenticated_at) AND (presence_at IS NOT NULL) AND (presence_at < expires_at) AND ((presence_section)::text = ANY ((ARRAY['welcome'::character varying, 'census'::character varying, 'members'::character varying, 'ministry'::character varying, 'financial'::character varying, 'additional'::character varying, 'review'::character varying])::text[]))))),
    CONSTRAINT family_session_chronology CHECK (((last_activity_at >= authenticated_at) AND (expires_at > last_activity_at))),
    CONSTRAINT family_session_mode_epoch CHECK (((((mode)::text = 'production'::text) AND (rehearsal_epoch_id IS NULL)) OR (((mode)::text = 'testing'::text) AND (rehearsal_epoch_id IS NOT NULL)))),
    CONSTRAINT stewardship_campaigns_familysession_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_family_session_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_family_token
CREATE TABLE public.stewardship_family_token (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    ciphertext text,
    digest character varying(64),
    destroyed_at timestamp with time zone,
    rotated_at timestamp with time zone,
    campaign_id uuid NOT NULL,
    generation_id uuid CONSTRAINT stewardship_family_token_generation_id_not_null1 NOT NULL,
    family_id uuid NOT NULL,
    CONSTRAINT family_token_destroyed_pair CHECK ((((ciphertext IS NOT NULL) AND (destroyed_at IS NULL) AND (digest IS NOT NULL)) OR ((ciphertext IS NULL) AND (destroyed_at IS NOT NULL) AND (digest IS NULL)))),
    CONSTRAINT stewardship_campaigns_familyaccesstoken_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_family_token_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_family_token_generation
CREATE TABLE public.stewardship_family_token_generation (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    operation_id uuid NOT NULL,
    task_id uuid,
    credential_epoch uuid NOT NULL,
    restore_id uuid,
    preparation_revision bigint CONSTRAINT stewardship_family_token_generati_preparation_revision_not_null NOT NULL,
    source_snapshot_id uuid NOT NULL,
    source_generation bigint NOT NULL,
    key_id character varying(48) NOT NULL,
    key_inventory_digest character varying(64) CONSTRAINT stewardship_family_token_generati_key_inventory_digest_not_null NOT NULL,
    coverage_digest character varying(64) NOT NULL,
    coverage_count bigint NOT NULL,
    checkpoint bigint NOT NULL,
    completed_at timestamp with time zone,
    state character varying(16) NOT NULL,
    campaign_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    configuration_request_id uuid,
    CONSTRAINT family_token_generation_state CHECK (((state)::text = ANY ((ARRAY['building'::character varying, 'ready'::character varying, 'active'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'superseded'::character varying])::text[]))),
    CONSTRAINT family_token_ready_manifest CHECK (((NOT ((state)::text = ANY ((ARRAY['ready'::character varying, 'active'::character varying])::text[]))) OR ((completed_at IS NOT NULL) AND ((coverage_digest)::text ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT stewardship_campaigns_familyaccesstokengeneration_positive_vers CHECK ((version >= 1)),
    CONSTRAINT stewardship_family_token_generation_checkpoint_check CHECK ((checkpoint >= 0)),
    CONSTRAINT stewardship_family_token_generation_coverage_count_check CHECK ((coverage_count >= 0)),
    CONSTRAINT stewardship_family_token_generation_preparation_revision_check CHECK ((preparation_revision >= 0)),
    CONSTRAINT stewardship_family_token_generation_source_generation_check CHECK ((source_generation >= 0)),
    CONSTRAINT stewardship_family_token_generation_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_limiter_health
CREATE TABLE public.stewardship_limiter_health (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    namespace_fingerprint character varying(64) NOT NULL,
    run_id character varying(40) NOT NULL,
    marker uuid NOT NULL,
    evicted_keys bigint NOT NULL,
    CONSTRAINT limiter_health_namespace CHECK (((namespace_fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT limiter_health_run_id CHECK (((run_id)::text ~ '^[0-9a-f]{40}$'::text)),
    CONSTRAINT stewardship_accounts_limiterstorehealth_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_limiter_health_evicted_keys_check CHECK ((evicted_keys >= 0)),
    CONSTRAINT stewardship_limiter_health_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_ministry_activity
CREATE TABLE public.stewardship_ministry_activity (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    ministry_duid bigint NOT NULL,
    active boolean NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT ministry_activity_duids CHECK (((ministry_duid > 0) AND (ministry_duid < '2147483648'::bigint) AND (organization_id > 0) AND (organization_id < '2147483648'::bigint))),
    CONSTRAINT stewardship_ministry_activity_ministry_duid_check CHECK ((ministry_duid >= 0)),
    CONSTRAINT stewardship_ministry_activity_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_ministry_assignment
CREATE TABLE public.stewardship_ministry_assignment (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    email character varying(254) NOT NULL,
    ministry_duid bigint NOT NULL,
    source character varying(16) NOT NULL,
    operation_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT stewardship_ministry_assignment_ministry_duid_check CHECK ((ministry_duid >= 0))
);

-- TABLE: stewardship_oauth_consumption
CREATE TABLE public.stewardship_oauth_consumption (
    fingerprint character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL
);

-- TABLE: stewardship_occurrence_transition
CREATE TABLE public.stewardship_occurrence_transition (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    version bigint NOT NULL,
    before_state character varying(24),
    after_state character varying(24) NOT NULL,
    fence bigint NOT NULL,
    attempts bigint NOT NULL,
    reason character varying(64) NOT NULL,
    retry_command_id uuid,
    occurrence_id uuid NOT NULL,
    CONSTRAINT stewardship_occurrence_transition_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT stewardship_occurrence_transition_fence_check CHECK ((fence >= 0)),
    CONSTRAINT stewardship_occurrence_transition_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_operational_log
CREATE TABLE public.stewardship_operational_log (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    level character varying(8) NOT NULL,
    event character varying(64) NOT NULL,
    schema character varying(16) NOT NULL,
    context jsonb NOT NULL,
    CONSTRAINT operational_context_safe CHECK (public.stewardship_safe_context_v1((schema)::text, context)),
    CONSTRAINT operational_event_safe CHECK (event IN (
        'configuration_rejected','configuration_digest_mismatch','startup_rejected',
        'startup_validated','request_completed','task_started','task_completed',
        'task_failed','unstructured_log_suppressed','authentication_limits_weakened',
        'installer_request_failed','source_refresh_invalid','source_member_unusable',
        'source_refresh_held','source_credential_failed','source_provider_failed',
        'credential_handoff_key_mismatch','setup_credential_staged',
        'setup_credential_scrubbed','campaign_boundary_lag')),
    CONSTRAINT operational_log_level CHECK (((level)::text = ANY ((ARRAY['DEBUG'::character varying, 'INFO'::character varying, 'WARNING'::character varying, 'ERROR'::character varying, 'CRITICAL'::character varying])::text[])))
);

-- TABLE: stewardship_parish
CREATE TABLE public.stewardship_parish (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    name character varying(254) NOT NULL,
    website character varying(2048) NOT NULL,
    timezone character varying(254) NOT NULL,
    phone character varying(12) NOT NULL,
    large_logo_id uuid NOT NULL,
    menu_logo_id uuid NOT NULL,
    icon_logo_id uuid NOT NULL,
    favicon_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT parish_nonempty_identity CHECK (((NOT ((name)::text = ''::text)) AND (NOT ((timezone)::text = ''::text)))),
    CONSTRAINT parish_us_phone CHECK (((phone)::text ~ '^\+1[2-9][0-9]{2}[2-9][0-9]{6}$'::text)),
    CONSTRAINT parish_website_scheme CHECK (((website)::text ~* '^https?://'::text))
);

-- TABLE: stewardship_policy_epoch
CREATE TABLE public.stewardship_policy_epoch (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    sequence bigint NOT NULL,
    activation_id uuid NOT NULL,
    CONSTRAINT stewardship_policy_epoch_sequence_check CHECK ((sequence >= 0))
);

-- TABLE: stewardship_policy_security_event
CREATE TABLE public.stewardship_policy_security_event (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    rule_record_id uuid NOT NULL,
    target character varying(254) NOT NULL,
    kind character varying(32) NOT NULL,
    before_roles jsonb NOT NULL,
    after_roles jsonb NOT NULL,
    recipients jsonb NOT NULL,
    activation_id uuid NOT NULL
);

-- TABLE: stewardship_portal_session
CREATE TABLE public.stewardship_portal_session (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    principal_id uuid NOT NULL,
    authenticated_at timestamp with time zone NOT NULL,
    last_activity_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    session_id character varying(40) NOT NULL,
    CONSTRAINT portal_session_activity_after_auth CHECK ((last_activity_at >= authenticated_at)),
    CONSTRAINT portal_session_activity_before_expiry CHECK ((last_activity_at < expires_at)),
    CONSTRAINT portal_session_no_activity_after_revoke CHECK (((revoked_at IS NULL) OR (revoked_at >= last_activity_at))),
    CONSTRAINT portal_session_positive_lifetime CHECK ((expires_at > authenticated_at)),
    CONSTRAINT portal_session_revoked_after_auth CHECK (((revoked_at IS NULL) OR (revoked_at >= authenticated_at))),
    CONSTRAINT stewardship_accounts_portalsession_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_portal_session_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_portal_user
CREATE TABLE public.stewardship_portal_user (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    google_subject character varying(255) NOT NULL,
    email character varying(254) NOT NULL,
    hosted_domain character varying(253),
    verified_at timestamp with time zone NOT NULL,
    disabled boolean NOT NULL,
    CONSTRAINT stewardship_accounts_portaluser_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_portal_user_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_postclose_resolution
CREATE TABLE public.stewardship_postclose_resolution (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    mode character varying(16) NOT NULL,
    obligation_key character varying(256) NOT NULL,
    coverage_digest character varying(64) NOT NULL,
    coverage jsonb NOT NULL,
    reason character varying(1024) NOT NULL,
    outbox_id uuid,
    campaign_id uuid NOT NULL,
    task_id uuid,
    occurrence_id uuid,
    CONSTRAINT postclose_coverage_digest CHECK (((coverage_digest)::text ~ '^[0-9a-f]{64}$'::text))
);

-- TABLE: stewardship_provider_context
CREATE TABLE public.stewardship_provider_context (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    target character varying(32) NOT NULL,
    settings jsonb NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT provider_context_known_target CHECK (((target)::text = ANY ((ARRAY['parishsoft'::character varying, 'google_workspace'::character varying, 'slack'::character varying])::text[])))
);

ALTER TABLE ONLY public.stewardship_provider_context FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_public_credential_handoff
CREATE TABLE public.stewardship_public_credential_handoff (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    target character varying(32) NOT NULL,
    key_id character varying(48) NOT NULL,
    public_key bytea NOT NULL,
    CONSTRAINT handoff_public_key_label CHECK (((key_id)::text ~ '^[A-Za-z0-9_-]{1,48}$'::text)),
    CONSTRAINT handoff_public_known_target CHECK (((target)::text = ANY ((ARRAY['django_signing'::character varying, 'general_encryption'::character varying, 'family_code_mac'::character varying, 'token_public'::character varying, 'token_private'::character varying, 'google_oauth'::character varying, 'google_workspace'::character varying, 'parishsoft'::character varying, 'slack'::character varying, 'backup_target'::character varying, 'backup_data'::character varying, 'metrics'::character varying])::text[])))
);

ALTER TABLE ONLY public.stewardship_public_credential_handoff FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_rehearsal_code_mac
CREATE TABLE public.stewardship_rehearsal_code_mac (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    key_id character varying(48) NOT NULL,
    algorithm character varying(24) NOT NULL,
    digest character varying(64) NOT NULL,
    credential_id uuid NOT NULL,
    epoch_id uuid NOT NULL,
    CONSTRAINT rehearsal_code_mac_format CHECK ((((algorithm)::text = 'hmac-sha256-v1'::text) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: stewardship_rehearsal_credential
CREATE TABLE public.stewardship_rehearsal_credential (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    code_ciphertext text NOT NULL,
    token_ciphertext text NOT NULL,
    token_digest character varying(64) NOT NULL,
    family_id uuid NOT NULL,
    epoch_id uuid NOT NULL,
    CONSTRAINT stewardship_campaigns_rehearsalcredential_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_rehearsal_credential_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_rehearsal_epoch
CREATE TABLE public.stewardship_rehearsal_epoch (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    invalidated_at timestamp with time zone,
    state character varying(16) NOT NULL,
    campaign_id uuid NOT NULL,
    CONSTRAINT rehearsal_epoch_state CHECK ((((invalidated_at IS NULL) AND ((state)::text = 'active'::text)) OR ((invalidated_at IS NOT NULL) AND ((state)::text = 'invalidated'::text)))),
    CONSTRAINT stewardship_campaigns_rehearsalepoch_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_rehearsal_epoch_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_rehearsal_reservation
CREATE TABLE public.stewardship_rehearsal_reservation (
    id uuid NOT NULL,
    key_id character varying(48) NOT NULL,
    algorithm character varying(24) NOT NULL,
    digest character varying(64) NOT NULL,
    campaign_id uuid NOT NULL,
    CONSTRAINT rehearsal_reservation_format CHECK ((((algorithm)::text = 'hmac-sha256-v1'::text) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text)))
);

-- TABLE: stewardship_restore_delivery_hold
CREATE TABLE public.stewardship_restore_delivery_hold (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    restore_id uuid NOT NULL,
    mode character varying(16) NOT NULL,
    target character varying(128) NOT NULL,
    slot character varying(128) NOT NULL,
    backup_at timestamp with time zone NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    discovery character varying(64) NOT NULL,
    state character varying(24) NOT NULL,
    resolved_at timestamp with time zone,
    evidence character varying(1024) NOT NULL,
    definition_id uuid NOT NULL,
    recovery_occurrence_id uuid,
    CONSTRAINT restore_hold_state CHECK (((state)::text = ANY ((ARRAY['unreviewed'::character varying, 'assumed_delivered'::character varying, 'resend_authorized'::character varying, 'not_applicable'::character varying])::text[]))),
    CONSTRAINT restore_hold_window CHECK ((window_end >= window_start)),
    CONSTRAINT stewardship_campaigns_restoredeliveryhold_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_restore_delivery_hold_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_restore_hold_resolution
CREATE TABLE public.stewardship_restore_hold_resolution (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    version bigint NOT NULL,
    state character varying(24) NOT NULL,
    evidence character varying(1024) NOT NULL,
    hold_id uuid NOT NULL,
    recovery_occurrence_id uuid,
    CONSTRAINT stewardship_restore_hold_resolution_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_runtime_transition
CREATE TABLE public.stewardship_runtime_transition (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    request_id uuid NOT NULL,
    expected_version bigint NOT NULL,
    action character varying(24) NOT NULL,
    before_mode character varying(16) NOT NULL,
    after_mode character varying(16) NOT NULL,
    before_campaign_id uuid,
    after_campaign_id uuid,
    restore_id uuid,
    backup_at timestamp with time zone,
    reason character varying(1024) NOT NULL,
    campaign_transition_id uuid,
    CONSTRAINT stewardship_runtime_transition_expected_version_check CHECK ((expected_version >= 0))
);

-- TABLE: stewardship_schedule_definition
CREATE TABLE public.stewardship_schedule_definition (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    kind character varying(24) NOT NULL,
    removed_at timestamp with time zone,
    campaign_id uuid NOT NULL,
    current_revision_id uuid,
    CONSTRAINT schedule_definition_kind CHECK (((kind)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying, 'daily_digest'::character varying, 'weekly_digest'::character varying])::text[]))),
    CONSTRAINT schedule_selection_shape CHECK ((((current_revision_id IS NOT NULL) AND (removed_at IS NULL)) OR ((current_revision_id IS NULL) AND (removed_at IS NOT NULL)))),
    CONSTRAINT stewardship_campaigns_scheduledefinition_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_schedule_definition_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_schedule_fulfillment
CREATE TABLE public.stewardship_schedule_fulfillment (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    mode character varying(16) NOT NULL,
    target character varying(128) NOT NULL,
    slot character varying(128) NOT NULL,
    disposition character varying(16) NOT NULL,
    definition_id uuid NOT NULL,
    occurrence_id uuid NOT NULL,
    CONSTRAINT schedule_fulfillment_disposition CHECK (((disposition)::text = ANY ((ARRAY['delivered'::character varying, 'coalesced'::character varying])::text[])))
);

-- TABLE: stewardship_schedule_occurrence
CREATE TABLE public.stewardship_schedule_occurrence (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    mode character varying(16) NOT NULL,
    routing character varying(24) NOT NULL,
    target character varying(128) NOT NULL,
    slot character varying(128) NOT NULL,
    due_at timestamp with time zone NOT NULL,
    occurrence_key character varying(64) NOT NULL,
    state character varying(24) NOT NULL,
    outbox_id uuid,
    worker_id uuid,
    fence bigint NOT NULL,
    attempts bigint NOT NULL,
    lease_expires_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    reason character varying(64) NOT NULL,
    pause_version bigint,
    retry_command_id uuid,
    definition_id uuid NOT NULL,
    replacement_id uuid,
    revision_id uuid NOT NULL,
    task_id uuid,
    CONSTRAINT schedule_occurrence_lease CHECK ((((heartbeat_at IS NOT NULL) AND (lease_expires_at IS NOT NULL) AND ((state)::text = 'running'::text) AND (task_id IS NOT NULL) AND (worker_id IS NOT NULL)) OR ((NOT ((state)::text = 'running'::text)) AND (lease_expires_at IS NULL)))),
    CONSTRAINT schedule_occurrence_replacement CHECK ((((replacement_id IS NOT NULL) AND ((state)::text = 'coalesced'::text)) OR ((NOT ((state)::text = 'coalesced'::text)) AND (replacement_id IS NULL)))),
    CONSTRAINT schedule_occurrence_routing CHECK (((((mode)::text = 'production'::text) AND ((routing)::text = 'production'::text)) OR (((mode)::text = 'testing'::text) AND ((routing)::text = 'testing_override'::text)))),
    CONSTRAINT schedule_occurrence_state CHECK (((state)::text = ANY ((ARRAY['pending'::character varying, 'running'::character varying, 'delivery_unknown'::character varying, 'succeeded'::character varying, 'skipped'::character varying, 'coalesced'::character varying, 'failed'::character varying])::text[]))),
    CONSTRAINT stewardship_campaigns_scheduleoccurrence_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_schedule_occurrence_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT stewardship_schedule_occurrence_fence_check CHECK ((fence >= 0)),
    CONSTRAINT stewardship_schedule_occurrence_pause_version_check CHECK ((pause_version >= 0)),
    CONSTRAINT stewardship_schedule_occurrence_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_schedule_revision
CREATE TABLE public.stewardship_schedule_revision (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    record_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    kind character varying(24) NOT NULL,
    due_at timestamp with time zone,
    "values" jsonb NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT schedule_revision_due_kind CHECK ((((due_at IS NOT NULL) AND ((kind)::text = ANY ((ARRAY['initial'::character varying, 'reminder'::character varying])::text[]))) OR ((due_at IS NULL) AND ((kind)::text = ANY ((ARRAY['daily_digest'::character varying, 'weekly_digest'::character varying])::text[])))))
);

-- TABLE: stewardship_schedule_selection
CREATE TABLE public.stewardship_schedule_selection (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    version bigint NOT NULL,
    configuration_id uuid NOT NULL,
    definition_id uuid NOT NULL,
    previous_revision_id uuid,
    selected_revision_id uuid,
    CONSTRAINT stewardship_schedule_selection_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_sealed_credential_staging
CREATE TABLE public.stewardship_sealed_credential_staging (
    reference uuid NOT NULL,
    target character varying(32) NOT NULL,
    ciphertext text,
    fingerprint character varying(64) NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT sealed_staging_fingerprint CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT sealed_staging_target CHECK (((target)::text = ANY ((ARRAY['django_signing'::character varying, 'general_encryption'::character varying, 'family_code_mac'::character varying, 'token_public'::character varying, 'token_private'::character varying, 'google_oauth'::character varying, 'google_workspace'::character varying, 'parishsoft'::character varying, 'slack'::character varying, 'backup_target'::character varying, 'backup_data'::character varying, 'metrics'::character varying])::text[])))
);

ALTER TABLE ONLY public.stewardship_sealed_credential_staging FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_secret_checkpoint
CREATE TABLE public.stewardship_secret_checkpoint (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    sequence bigint NOT NULL,
    state character varying(16) NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT secret_checkpoint_positive CHECK ((sequence >= 1)),
    CONSTRAINT secret_checkpoint_state CHECK (((state)::text = ANY ((ARRAY['staged'::character varying, 'testing'::character varying, 'installing'::character varying, 'awaiting_ack'::character varying, 'cleanup_pending'::character varying, 'cancelled'::character varying, 'expired'::character varying, 'failed'::character varying, 'applied'::character varying])::text[]))),
    CONSTRAINT stewardship_secret_checkpoint_sequence_check CHECK ((sequence >= 0))
);

-- TABLE: stewardship_secret_request
CREATE TABLE public.stewardship_secret_request (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    target character varying(32) NOT NULL,
    staging_reference uuid NOT NULL,
    requested_by_id uuid NOT NULL,
    reauthenticated_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    expected_fingerprint character varying(64),
    state character varying(16) DEFAULT 'staged'::character varying NOT NULL,
    cleanup_reason character varying(16) DEFAULT ''::character varying NOT NULL,
    scrubbed_at timestamp with time zone,
    acknowledged_at timestamp with time zone,
    installed_at timestamp with time zone,
    required_consumers jsonb NOT NULL,
    resulting_fingerprint character varying(64),
    CONSTRAINT secret_applied_has_ack CHECK (((NOT ((state)::text = 'applied'::text)) OR (acknowledged_at IS NOT NULL))),
    CONSTRAINT secret_cleanup_shape CHECK (((((cleanup_reason)::text = ''::text) AND (scrubbed_at IS NULL) AND ((state)::text = ANY ((ARRAY['staged'::character varying, 'testing'::character varying, 'installing'::character varying, 'awaiting_ack'::character varying])::text[]))) OR (((cleanup_reason)::text = ANY ((ARRAY['cancelled'::character varying, 'expired'::character varying, 'failed'::character varying, 'applied'::character varying])::text[])) AND (scrubbed_at IS NULL) AND ((state)::text = 'cleanup_pending'::text)) OR (((cleanup_reason)::text = (state)::text) AND (scrubbed_at IS NOT NULL) AND ((state)::text = ANY ((ARRAY['cancelled'::character varying, 'expired'::character varying, 'failed'::character varying, 'applied'::character varying])::text[])) AND (scrubbed_at >= created_at)))),
    CONSTRAINT secret_install_has_fingerprint CHECK (((NOT ((state)::text = ANY ((ARRAY['installing'::character varying, 'awaiting_ack'::character varying, 'applied'::character varying])::text[]))) OR (resulting_fingerprint IS NOT NULL))),
    CONSTRAINT secret_installed_has_instant CHECK (((NOT ((state)::text = ANY ((ARRAY['awaiting_ack'::character varying, 'applied'::character varying])::text[]))) OR (installed_at IS NOT NULL))),
    CONSTRAINT secret_known_state CHECK (((state)::text = ANY ((ARRAY['staged'::character varying, 'testing'::character varying, 'installing'::character varying, 'awaiting_ack'::character varying, 'cleanup_pending'::character varying, 'cancelled'::character varying, 'expired'::character varying, 'failed'::character varying, 'applied'::character varying])::text[]))),
    CONSTRAINT secret_known_target CHECK (((target)::text = ANY ((ARRAY['django_signing'::character varying, 'general_encryption'::character varying, 'family_code_mac'::character varying, 'token_public'::character varying, 'token_private'::character varying, 'google_oauth'::character varying, 'google_workspace'::character varying, 'parishsoft'::character varying, 'slack'::character varying, 'backup_target'::character varying, 'backup_data'::character varying, 'metrics'::character varying])::text[]))),
    CONSTRAINT secret_max_staging_lifetime CHECK ((expires_at <= (created_at + '1 day'::interval))),
    CONSTRAINT secret_result_fingerprint CHECK (((resulting_fingerprint IS NULL) OR ((resulting_fingerprint)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT secret_safe_fingerprint CHECK (((expected_fingerprint IS NULL) OR ((expected_fingerprint)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT secret_valid_interval CHECK (((reauthenticated_at <= created_at) AND (expires_at > created_at))),
    CONSTRAINT stewardship_accounts_secretreplacementrequest_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_secret_request_version_check CHECK ((version >= 0))
);

ALTER TABLE ONLY public.stewardship_secret_request FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_setup_attempt
CREATE TABLE public.stewardship_setup_attempt (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    session_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    state character varying(16) NOT NULL,
    renewed_at timestamp with time zone,
    expired_at timestamp with time zone,
    expiry_reason character varying(16) NOT NULL,
    base_id uuid NOT NULL,
    source_task_id uuid,
    CONSTRAINT setup_expiry_shape CHECK ((((expired_at IS NOT NULL) AND ((expiry_reason)::text = ANY ((ARRAY['cancelled'::character varying, 'session'::character varying, 'idle'::character varying, 'watchdog'::character varying, 'absolute'::character varying])::text[])) AND ((state)::text = 'expired'::text)) OR ((NOT ((state)::text = 'expired'::text)) AND (expired_at IS NULL) AND ((expiry_reason)::text = ''::text)))),
    CONSTRAINT setup_known_state CHECK (((state)::text = ANY ((ARRAY['collecting'::character varying, 'loading'::character varying, 'frozen'::character varying, 'completed'::character varying, 'expired'::character varying])::text[]))),
    CONSTRAINT setup_loading_task CHECK (((NOT ((state)::text = 'loading'::text)) OR (source_task_id IS NOT NULL))),
    CONSTRAINT stewardship_accounts_setupattempt_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_attempt_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_setup_completion
CREATE TABLE public.stewardship_setup_completion (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    task_fence bigint NOT NULL,
    source_fence bigint NOT NULL,
    activation_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT setup_completion_fences CHECK (((source_fence > 0) AND (task_fence > 0))),
    CONSTRAINT stewardship_setup_completion_source_fence_check CHECK ((source_fence >= 0)),
    CONSTRAINT stewardship_setup_completion_task_fence_check CHECK ((task_fence >= 0))
);

-- TABLE: stewardship_setup_config_abort
CREATE TABLE public.stewardship_setup_config_abort (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    intent_id uuid NOT NULL,
    reason character varying(16) NOT NULL,
    CONSTRAINT setup_abort_reason CHECK (((reason)::text = ANY ((ARRAY['cancelled'::character varying, 'session'::character varying, 'idle'::character varying, 'watchdog'::character varying, 'absolute'::character varying])::text[])))
);

-- TABLE: stewardship_setup_config_intent
CREATE TABLE public.stewardship_setup_config_intent (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    attempt_version bigint NOT NULL,
    attempt_id uuid NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT setup_intent_positive_version CHECK ((attempt_version >= 1)),
    CONSTRAINT stewardship_setup_config_intent_attempt_version_check CHECK ((attempt_version >= 0))
);

-- TABLE: stewardship_setup_credential_install
CREATE TABLE public.stewardship_setup_credential_install (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    target character varying(32) NOT NULL,
    credential_version bigint CONSTRAINT stewardship_setup_credential_instal_credential_version_not_null NOT NULL,
    fingerprint character varying(64) NOT NULL,
    credential_id uuid NOT NULL,
    readiness_id uuid NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT setup_install_bound_version CHECK (((credential_version >= 1) AND ((fingerprint)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT setup_install_known_target CHECK (((target)::text = ANY ((ARRAY['parishsoft'::character varying, 'google_workspace'::character varying, 'slack'::character varying])::text[]))),
    CONSTRAINT stewardship_setup_credential_install_credential_version_check CHECK ((credential_version >= 0))
);

-- TABLE: stewardship_setup_draft_section
CREATE TABLE public.stewardship_setup_draft_section (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    step character varying(48) NOT NULL,
    "values" jsonb NOT NULL,
    scrubbed_at timestamp with time zone,
    attempt_id uuid NOT NULL,
    scope_digest character varying(64) GENERATED ALWAYS AS (encode(sha256(jsonb_send("values")), 'hex'::text)) STORED,
    CONSTRAINT setup_public_step CHECK (((step)::text = ANY ((ARRAY['parish'::character varying, 'branding'::character varying, 'access'::character varying, 'mail'::character varying, 'slack'::character varying, 'testing'::character varying, 'campaign'::character varying, 'schedules'::character varying, 'page_access_denied'::character varying, 'page_additional'::character varying, 'page_census'::character varying, 'page_financial'::character varying, 'page_login_help'::character varying, 'page_member_census'::character varying, 'page_ministry'::character varying, 'page_post_end'::character varying, 'page_pre_start'::character varying, 'page_review'::character varying, 'page_submission_confirmation'::character varying, 'page_thank_you'::character varying, 'page_welcome'::character varying, 'email_confirmation'::character varying, 'email_critical_alert'::character varying, 'email_daily_digest'::character varying, 'email_initial'::character varying, 'email_reminder'::character varying, 'email_weekly_digest'::character varying])::text[]))),
    CONSTRAINT setup_scrubbed_values_empty CHECK (((scrubbed_at IS NULL) OR ("values" = '{}'::jsonb))),
    CONSTRAINT stewardship_accounts_setupdraftsection_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_draft_section_version_check CHECK ((version >= 0))
);

ALTER TABLE ONLY public.stewardship_setup_draft_section FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_setup_mail_delivery
CREATE TABLE public.stewardship_setup_mail_delivery (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    attempt_version bigint NOT NULL,
    request_key uuid NOT NULL,
    candidate_digest character varying(64) NOT NULL,
    credential_version bigint NOT NULL,
    fingerprint character varying(64) NOT NULL,
    mail jsonb NOT NULL,
    state character varying(24) NOT NULL,
    task_fence bigint,
    worker_id uuid,
    submitted_at timestamp with time zone,
    deadline_at timestamp with time zone,
    finished_at timestamp with time zone,
    scrubbed_at timestamp with time zone,
    attempt_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    run_id uuid,
    task_id uuid NOT NULL,
    CONSTRAINT setup_mail_digest_shape CHECK ((((candidate_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((fingerprint)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT setup_mail_known_state CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying, 'accepted'::character varying, 'not_sent'::character varying, 'delivery_unknown'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT setup_mail_positive_bindings CHECK (((attempt_version >= 1) AND (credential_version >= 1))),
    CONSTRAINT setup_mail_scrubbed_payload CHECK (((scrubbed_at IS NULL) OR (mail = '{}'::jsonb))),
    CONSTRAINT setup_mail_submission_shape CHECK ((((deadline_at IS NULL) AND (run_id IS NULL) AND ((state)::text = ANY ((ARRAY['queued'::character varying, 'cancelled'::character varying])::text[])) AND (submitted_at IS NULL) AND (task_fence IS NULL) AND (worker_id IS NULL)) OR ((deadline_at IS NOT NULL) AND (run_id IS NOT NULL) AND ((state)::text = ANY ((ARRAY['submitting'::character varying, 'accepted'::character varying, 'not_sent'::character varying, 'delivery_unknown'::character varying])::text[])) AND (submitted_at IS NOT NULL) AND (task_fence >= 1) AND (worker_id IS NOT NULL)))),
    CONSTRAINT setup_mail_terminal_time CHECK ((((finished_at IS NULL) AND ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]))) OR ((finished_at IS NOT NULL) AND (NOT ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[])))))),
    CONSTRAINT stewardship_accounts_setupmaildelivery_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_mail_delivery_attempt_version_check CHECK ((attempt_version >= 0)),
    CONSTRAINT stewardship_setup_mail_delivery_credential_version_check CHECK ((credential_version >= 0)),
    CONSTRAINT stewardship_setup_mail_delivery_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_setup_mail_delivery_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_setup_mail_exchange
CREATE TABLE public.stewardship_setup_mail_exchange (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    task_fence bigint NOT NULL,
    worker_id uuid NOT NULL,
    public_key bytea NOT NULL,
    ciphertext text,
    replied_at timestamp with time zone,
    scrubbed_at timestamp with time zone,
    delivery_id uuid NOT NULL,
    run_id uuid NOT NULL,
    CONSTRAINT setup_mail_exchange_positive_fence CHECK ((task_fence >= 1)),
    CONSTRAINT setup_mail_exchange_reply_shape CHECK ((((ciphertext IS NULL) AND (replied_at IS NULL)) OR ((ciphertext IS NOT NULL) AND (replied_at IS NOT NULL) AND (scrubbed_at IS NULL)) OR ((ciphertext IS NULL) AND (scrubbed_at IS NOT NULL)))),
    CONSTRAINT stewardship_accounts_setupmailexchange_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_mail_exchange_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_setup_mail_exchange_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_setup_prepared
CREATE TABLE public.stewardship_setup_prepared (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    readiness_id uuid NOT NULL
);

-- TABLE: stewardship_setup_readiness_binding
CREATE TABLE public.stewardship_setup_readiness_binding (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    testing_recipient character varying(254) NOT NULL,
    intent_id uuid NOT NULL,
    mail_delivery_id uuid NOT NULL,
    slack_delivery_id uuid,
    source_result_id uuid NOT NULL
);

-- TABLE: stewardship_setup_sealed_credential
CREATE TABLE public.stewardship_setup_sealed_credential (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    target character varying(32) NOT NULL,
    ciphertext text,
    fingerprint character varying(64) NOT NULL,
    settings jsonb NOT NULL,
    scrubbed_at timestamp with time zone,
    attempt_id uuid NOT NULL,
    CONSTRAINT setup_secret_fingerprint CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT setup_secret_scrub_shape CHECK ((((ciphertext IS NOT NULL) AND (scrubbed_at IS NULL)) OR ((ciphertext IS NULL) AND (scrubbed_at IS NOT NULL)))),
    CONSTRAINT setup_secret_target CHECK (((target)::text = ANY ((ARRAY['parishsoft'::character varying, 'google_workspace'::character varying, 'slack'::character varying])::text[]))),
    CONSTRAINT stewardship_accounts_setupsealedcredential_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_sealed_credential_version_check CHECK ((version >= 0))
);

ALTER TABLE ONLY public.stewardship_setup_sealed_credential FORCE ROW LEVEL SECURITY;

-- TABLE: stewardship_setup_slack_delivery
CREATE TABLE public.stewardship_setup_slack_delivery (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    attempt_version bigint NOT NULL,
    request_key uuid NOT NULL,
    candidate_digest character varying(64) NOT NULL,
    credential_version bigint NOT NULL,
    fingerprint character varying(64) NOT NULL,
    state character varying(24) NOT NULL,
    worker_id uuid,
    submitted_at timestamp with time zone,
    deadline_at timestamp with time zone,
    finished_at timestamp with time zone,
    attempt_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    CONSTRAINT setup_slack_digest_shape CHECK ((((candidate_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((fingerprint)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT setup_slack_known_state CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'cancelled'::character varying, 'submitting'::character varying, 'accepted'::character varying, 'not_sent'::character varying, 'delivery_unknown'::character varying])::text[]))),
    CONSTRAINT setup_slack_positive_bindings CHECK (((attempt_version >= 1) AND (credential_version >= 1))),
    CONSTRAINT setup_slack_submission_shape CHECK ((((deadline_at IS NULL) AND ((state)::text = ANY ((ARRAY['queued'::character varying, 'cancelled'::character varying])::text[])) AND (submitted_at IS NULL) AND (worker_id IS NULL)) OR ((deadline_at IS NOT NULL) AND ((state)::text = ANY ((ARRAY['submitting'::character varying, 'accepted'::character varying, 'not_sent'::character varying, 'delivery_unknown'::character varying])::text[])) AND (submitted_at IS NOT NULL) AND (worker_id IS NOT NULL)))),
    CONSTRAINT setup_slack_terminal_shape CHECK ((((finished_at IS NULL) AND ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]))) OR ((NOT ((state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying])::text[]))) AND (finished_at IS NOT NULL)))),
    CONSTRAINT stewardship_accounts_setupslackdelivery_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_slack_delivery_attempt_version_check CHECK ((attempt_version >= 0)),
    CONSTRAINT stewardship_setup_slack_delivery_credential_version_check CHECK ((credential_version >= 0)),
    CONSTRAINT stewardship_setup_slack_delivery_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_setup_source_exchange
CREATE TABLE public.stewardship_setup_source_exchange (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    credential_version bigint NOT NULL,
    fingerprint character varying(64) NOT NULL,
    task_fence bigint NOT NULL,
    worker_id uuid NOT NULL,
    source_fence bigint NOT NULL,
    public_key bytea NOT NULL,
    ciphertext text,
    replied_at timestamp with time zone,
    scrubbed_at timestamp with time zone,
    attempt_id uuid NOT NULL,
    credential_id uuid NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT setup_exchange_fingerprint CHECK (((fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT setup_exchange_positive_fences CHECK (((credential_version >= 1) AND (source_fence >= 1) AND (task_fence >= 1))),
    CONSTRAINT setup_exchange_reply_shape CHECK ((((ciphertext IS NULL) AND (replied_at IS NULL)) OR ((ciphertext IS NOT NULL) AND (replied_at IS NOT NULL) AND (scrubbed_at IS NULL)) OR ((ciphertext IS NULL) AND (scrubbed_at IS NOT NULL)))),
    CONSTRAINT stewardship_accounts_setupsourceexchange_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_setup_source_exchange_credential_version_check CHECK ((credential_version >= 0)),
    CONSTRAINT stewardship_setup_source_exchange_source_fence_check CHECK ((source_fence >= 0)),
    CONSTRAINT stewardship_setup_source_exchange_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_setup_source_exchange_version_check CHECK ((version >= 0))
);

-- TABLE: stewardship_setup_source_result
CREATE TABLE public.stewardship_setup_source_result (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    exchange_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_address
CREATE TABLE public.stewardship_snapshot_address (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_contribution
CREATE TABLE public.stewardship_snapshot_contribution (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_family
CREATE TABLE public.stewardship_snapshot_family (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_fund
CREATE TABLE public.stewardship_snapshot_fund (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_snapshot_pledge
CREATE TABLE public.stewardship_snapshot_pledge (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_key character varying(200) NOT NULL,
    payload_id uuid NOT NULL,
    snapshot_id uuid NOT NULL
);

-- TABLE: stewardship_source_address
CREATE TABLE public.stewardship_source_address (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    owner_kind character varying(8) NOT NULL,
    owner_key character varying(200) NOT NULL,
    CONSTRAINT source_address_owner_kind CHECK (((owner_kind)::text = ANY ((ARRAY['family'::character varying, 'member'::character varying])::text[]))),
    CONSTRAINT sourceaddress_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_address_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_compaction
CREATE TABLE public.stewardship_source_compaction (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    source_fence bigint NOT NULL,
    cutoff_at timestamp with time zone NOT NULL,
    recent_cutoff timestamp with time zone NOT NULL,
    yearly_cutoff timestamp with time zone NOT NULL,
    snapshot_count bigint NOT NULL,
    membership_count bigint NOT NULL,
    payload_count bigint NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT source_compaction_cutoffs CHECK (((source_fence > 0) AND (yearly_cutoff < recent_cutoff) AND (recent_cutoff < cutoff_at))),
    CONSTRAINT stewardship_source_compaction_membership_count_check CHECK ((membership_count >= 0)),
    CONSTRAINT stewardship_source_compaction_payload_count_check CHECK ((payload_count >= 0)),
    CONSTRAINT stewardship_source_compaction_snapshot_count_check CHECK ((snapshot_count >= 0)),
    CONSTRAINT stewardship_source_compaction_source_fence_check CHECK ((source_fence >= 0))
);

-- TABLE: stewardship_source_contribution
CREATE TABLE public.stewardship_source_contribution (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    family_key character varying(200) NOT NULL,
    fund_key character varying(200) NOT NULL,
    CONSTRAINT sourcecontribution_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_contribution_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_family
CREATE TABLE public.stewardship_source_family (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    CONSTRAINT sourcefamily_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_family_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_fund
CREATE TABLE public.stewardship_source_fund (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    CONSTRAINT sourcefund_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_fund_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_lease
CREATE TABLE public.stewardship_source_lease (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    singleton boolean NOT NULL,
    task_fence bigint NOT NULL,
    worker_id uuid,
    fence bigint NOT NULL,
    phase character varying(16) NOT NULL,
    acquired_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    expires_at timestamp with time zone,
    external_deadline timestamp with time zone,
    owner_id uuid,
    CONSTRAINT source_lease_owner_shape CHECK ((((expires_at IS NULL) AND (owner_id IS NULL) AND ((phase)::text = 'idle'::text) AND (worker_id IS NULL)) OR ((acquired_at IS NOT NULL) AND (expires_at > heartbeat_at) AND (expires_at IS NOT NULL) AND (fence > 0) AND (heartbeat_at >= acquired_at) AND (heartbeat_at IS NOT NULL) AND (owner_id IS NOT NULL) AND ((phase)::text = ANY ((ARRAY['full'::character varying, 'delta'::character varying, 'publication'::character varying, 'compaction'::character varying])::text[])) AND (task_fence > 0) AND (worker_id IS NOT NULL)))),
    CONSTRAINT source_lease_singleton CHECK (singleton),
    CONSTRAINT stewardship_source_lease_fence_check CHECK ((fence >= 0)),
    CONSTRAINT stewardship_source_lease_task_fence_check CHECK ((task_fence >= 0)),
    CONSTRAINT stewardship_source_lease_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_source_sourcemutationlease_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_source_pin
CREATE TABLE public.stewardship_source_pin (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    parent_kind character varying(32) NOT NULL,
    parent_id uuid NOT NULL,
    expires_at timestamp with time zone,
    snapshot_id uuid NOT NULL,
    CONSTRAINT source_pin_expiry_owner CHECK ((((expires_at IS NULL) AND (NOT ((parent_kind)::text = 'form_baseline'::text))) OR ((expires_at IS NOT NULL) AND ((parent_kind)::text = 'form_baseline'::text)))),
    CONSTRAINT source_pin_kind CHECK (((parent_kind)::text = ANY ((ARRAY['submission'::character varying, 'report'::character varying, 'digest'::character varying, 'form_baseline'::character varying, 'publication'::character varying, 'audit'::character varying, 'boundary'::character varying, 'restore'::character varying, 'delivery_hold'::character varying, 'operator'::character varying, 'facts'::character varying])::text[]))),
    CONSTRAINT stewardship_source_pin_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_source_sourcesnapshotpin_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_source_pledge
CREATE TABLE public.stewardship_source_pledge (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    source_key character varying(200) NOT NULL,
    digest character varying(64) NOT NULL,
    canonical text NOT NULL,
    family_key character varying(200) NOT NULL,
    fund_key character varying(200) NOT NULL,
    CONSTRAINT sourcepledge_valid_identity CHECK (((organization_id > 0) AND (NOT ((source_key)::text = ''::text)) AND ((digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT stewardship_source_pledge_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_refresh_attempt
CREATE TABLE public.stewardship_source_refresh_attempt (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    task_fence bigint NOT NULL,
    credential_fingerprint character varying(64) CONSTRAINT stewardship_source_refresh_atte_credential_fingerprint_not_null NOT NULL,
    configuration_id uuid NOT NULL,
    request_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT source_attempt_credential CHECK (((credential_fingerprint)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT source_attempt_fence CHECK ((task_fence > 0)),
    CONSTRAINT stewardship_source_refresh_attempt_task_fence_check CHECK ((task_fence >= 0))
);

-- TABLE: stewardship_source_refresh_command
CREATE TABLE public.stewardship_source_refresh_command (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    kind character varying(8) NOT NULL,
    cause character varying(16) NOT NULL,
    request_id uuid NOT NULL,
    CONSTRAINT source_refresh_command_cause CHECK (((cause)::text = ANY ((ARRAY['manual'::character varying, 'nightly'::character varying, 'initial'::character varying, 'delta'::character varying, 'fallback'::character varying])::text[]))),
    CONSTRAINT source_refresh_command_cause_kind CHECK (((((cause)::text = 'delta'::text) AND ((kind)::text = 'delta'::text)) OR ((NOT ((cause)::text = 'delta'::text)) AND ((kind)::text = 'full'::text)))),
    CONSTRAINT source_refresh_command_kind CHECK (((kind)::text = ANY ((ARRAY['full'::character varying, 'delta'::character varying])::text[]))),
    CONSTRAINT source_refresh_manual_actor CHECK (((NOT ((cause)::text = 'manual'::text)) OR (actor_id IS NOT NULL)))
);

-- TABLE: stewardship_source_refresh_fallback
CREATE TABLE public.stewardship_source_refresh_fallback (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    task_fence bigint NOT NULL,
    reason character varying(24) NOT NULL,
    attempt_id uuid,
    command_id uuid NOT NULL,
    request_id uuid NOT NULL,
    task_id uuid NOT NULL,
    CONSTRAINT source_fallback_fence CHECK ((task_fence > 0)),
    CONSTRAINT source_fallback_reason CHECK ((((attempt_id IS NULL) AND ((reason)::text = 'no_base'::text)) OR ((attempt_id IS NOT NULL) AND ((reason)::text = 'incomplete_delta'::text)))),
    CONSTRAINT stewardship_source_refresh_fallback_task_fence_check CHECK ((task_fence >= 0))
);

-- TABLE: stewardship_source_refresh_request
CREATE TABLE public.stewardship_source_refresh_request (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    organization_id bigint NOT NULL,
    kind character varying(8) NOT NULL,
    window_canonical text NOT NULL,
    window_digest character varying(64) NOT NULL,
    campaign_id uuid,
    configuration_id uuid NOT NULL,
    task_root_id uuid NOT NULL,
    CONSTRAINT source_refresh_kind CHECK (((kind)::text = ANY ((ARRAY['full'::character varying, 'delta'::character varying])::text[]))),
    CONSTRAINT source_refresh_organization CHECK (((organization_id > 0) AND (organization_id <= 2147483647))),
    CONSTRAINT source_refresh_window_digest CHECK (((window_digest)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT stewardship_source_refresh_request_organization_id_check CHECK ((organization_id >= 0))
);

-- TABLE: stewardship_source_refresh_tick
CREATE TABLE public.stewardship_source_refresh_tick (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    due_at timestamp with time zone NOT NULL,
    timezone character varying(128) NOT NULL,
    nightly_time character varying(5) NOT NULL,
    slot_key character varying(64) NOT NULL,
    command_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    CONSTRAINT source_tick_key CHECK (((slot_key)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT source_tick_time CHECK (((nightly_time)::text ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'::text))
);

-- TABLE: stewardship_source_snapshot
CREATE TABLE public.stewardship_source_snapshot (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    organization_id bigint NOT NULL,
    kind character varying(8) NOT NULL,
    state character varying(12) NOT NULL,
    source_fence bigint NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    promoted_at timestamp with time zone,
    compacted_at timestamp with time zone,
    generation bigint,
    counts jsonb NOT NULL,
    validation jsonb NOT NULL,
    cursor jsonb NOT NULL,
    content_digest character varying(64) NOT NULL,
    base_id uuid,
    task_id uuid NOT NULL,
    CONSTRAINT source_snapshot_compaction_shape CHECK (((compacted_at IS NULL) OR ((compacted_at >= promoted_at) AND ((state)::text = 'promoted'::text)))),
    CONSTRAINT source_snapshot_digest CHECK ((((content_digest)::text = ''::text) OR ((content_digest)::text ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT source_snapshot_identity CHECK (((organization_id > 0) AND (source_fence > 0))),
    CONSTRAINT source_snapshot_kind CHECK (((kind)::text = ANY ((ARRAY['full'::character varying, 'delta'::character varying])::text[]))),
    CONSTRAINT source_snapshot_promotion_shape CHECK ((((generation > 0) AND (generation IS NOT NULL) AND (promoted_at IS NOT NULL) AND ((state)::text = 'promoted'::text)) OR ((NOT ((state)::text = 'promoted'::text)) AND (generation IS NULL) AND (promoted_at IS NULL)))),
    CONSTRAINT source_snapshot_state CHECK (((state)::text = ANY ((ARRAY['staging'::character varying, 'ready'::character varying, 'rejected'::character varying, 'promoted'::character varying])::text[]))),
    CONSTRAINT stewardship_source_snapshot_generation_check CHECK ((generation >= 0)),
    CONSTRAINT stewardship_source_snapshot_organization_id_check CHECK ((organization_id >= 0)),
    CONSTRAINT stewardship_source_snapshot_source_fence_check CHECK ((source_fence >= 0)),
    CONSTRAINT stewardship_source_snapshot_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_source_sourcesnapshot_positive_version CHECK ((version >= 1))
);

-- TABLE: stewardship_system_configuration
CREATE TABLE public.stewardship_system_configuration (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    mode character varying(16) NOT NULL,
    testing_recipient character varying(254) NOT NULL,
    restore_review_required boolean CONSTRAINT stewardship_system_configurati_restore_review_required_not_null NOT NULL,
    active_configuration_id uuid,
    current_campaign_id uuid,
    configuration_sequence bigint DEFAULT 1 CONSTRAINT stewardship_system_configuratio_configuration_sequence_not_null NOT NULL,
    restore_activated_at timestamp with time zone,
    restore_backup_at timestamp with time zone,
    restore_id uuid,
    restore_released_at timestamp with time zone,
    CONSTRAINT stewardship_accounts_systemconfiguration_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_system_configuration_configuration_sequence_check CHECK ((configuration_sequence >= 0)),
    CONSTRAINT stewardship_system_configuration_version_check CHECK ((version >= 0)),
    CONSTRAINT system_known_mode CHECK (((mode)::text = ANY ((ARRAY['testing'::character varying, 'production'::character varying])::text[]))),
    CONSTRAINT system_testing_recipient CHECK (((testing_recipient)::text ~ '^[^\s@]+@[^\s@]+\.[^\s@]+$'::text))
);

-- TABLE: stewardship_task_event
CREATE TABLE public.stewardship_task_event (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    version bigint NOT NULL,
    previous_state character varying(16) NOT NULL,
    state character varying(16) NOT NULL,
    action character varying(24) NOT NULL,
    attempt bigint NOT NULL,
    fence bigint NOT NULL,
    worker_id uuid,
    heartbeat_at timestamp with time zone,
    lease_expires_at timestamp with time zone,
    not_before timestamp with time zone NOT NULL,
    progress_current bigint NOT NULL,
    progress_total bigint NOT NULL,
    run_id uuid NOT NULL,
    phase character varying(32) DEFAULT 'unspecified'::character varying NOT NULL,
    CONSTRAINT stewardship_task_event_attempt_check CHECK ((attempt >= 0)),
    CONSTRAINT stewardship_task_event_fence_check CHECK ((fence >= 0)),
    CONSTRAINT stewardship_task_event_progress_current_check CHECK ((progress_current >= 0)),
    CONSTRAINT stewardship_task_event_progress_total_check CHECK ((progress_total >= 0)),
    CONSTRAINT stewardship_task_event_version_check CHECK ((version >= 0)),
    CONSTRAINT task_event_phase_known CHECK (((phase)::text = ANY ((ARRAY['unspecified'::character varying, 'starting'::character varying, 'preparing'::character varying, 'fetching'::character varying, 'validating'::character varying, 'staging'::character varying, 'promoting'::character varying, 'reconciling'::character varying, 'rendering'::character varying, 'delivering'::character varying, 'verifying'::character varying, 'compacting'::character varying, 'draining'::character varying, 'deleting'::character varying, 'uploading'::character varying])::text[]))),
    CONSTRAINT task_event_positive CHECK ((version >= 1))
);

-- TABLE: stewardship_task_run
CREATE TABLE public.stewardship_task_run (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    task_type character varying(64) NOT NULL,
    idempotency_key character varying(80),
    retry_sequence bigint NOT NULL,
    retry_command_id uuid,
    domain_request_id uuid,
    initiated_by_id uuid,
    state character varying(16) DEFAULT 'queued'::character varying NOT NULL,
    action character varying(24) DEFAULT 'created'::character varying NOT NULL,
    attempt bigint DEFAULT 0 NOT NULL,
    fence bigint DEFAULT 0 NOT NULL,
    worker_id uuid,
    heartbeat_at timestamp with time zone,
    lease_expires_at timestamp with time zone,
    not_before timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    progress_current bigint DEFAULT 0 NOT NULL,
    progress_total bigint DEFAULT 0 NOT NULL,
    parent_id uuid,
    root_id uuid NOT NULL,
    phase character varying(32) DEFAULT 'unspecified'::character varying NOT NULL,
    CONSTRAINT stewardship_jobs_taskrun_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_task_run_attempt_check CHECK ((attempt >= 0)),
    CONSTRAINT stewardship_task_run_fence_check CHECK ((fence >= 0)),
    CONSTRAINT stewardship_task_run_progress_current_check CHECK ((progress_current >= 0)),
    CONSTRAINT stewardship_task_run_progress_total_check CHECK ((progress_total >= 0)),
    CONSTRAINT stewardship_task_run_retry_sequence_check CHECK ((retry_sequence >= 0)),
    CONSTRAINT stewardship_task_run_version_check CHECK ((version >= 0)),
    CONSTRAINT task_claim_shape CHECK ((((heartbeat_at IS NOT NULL) AND (lease_expires_at IS NOT NULL) AND ((state)::text = 'running'::text) AND (worker_id IS NOT NULL)) OR ((NOT ((state)::text = 'running'::text)) AND (lease_expires_at IS NULL)))),
    CONSTRAINT task_fence_bounds CHECK ((fence >= attempt)),
    CONSTRAINT task_known_action CHECK (((action)::text = ANY ((ARRAY['created'::character varying, 'explicit_retry'::character varying, 'claim'::character varying, 'heartbeat'::character varying, 'progress'::character varying, 'complete'::character varying, 'retryable_failure'::character varying, 'permanent_failure'::character varying, 'safe_cancel'::character varying, 'lease_expired'::character varying, 'recovery_retry'::character varying, 'recovery_complete'::character varying, 'recovery_fail'::character varying, 'recovery_cancel'::character varying])::text[]))),
    CONSTRAINT task_known_state CHECK (((state)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'retry_wait'::character varying, 'abandoned'::character varying, 'succeeded'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT task_phase_known CHECK (((phase)::text = ANY ((ARRAY['unspecified'::character varying, 'starting'::character varying, 'preparing'::character varying, 'fetching'::character varying, 'validating'::character varying, 'staging'::character varying, 'promoting'::character varying, 'reconciling'::character varying, 'rendering'::character varying, 'delivering'::character varying, 'verifying'::character varying, 'compacting'::character varying, 'draining'::character varying, 'deleting'::character varying, 'uploading'::character varying])::text[]))),
    CONSTRAINT task_progress_bounds CHECK ((progress_current <= progress_total)),
    CONSTRAINT task_type_identifier CHECK (((task_type)::text ~ '^[a-z][a-z0-9_]{0,63}$'::text))
);
-- Phase 3A fresh-install response records.
CREATE TABLE "stewardship_family_form_baseline" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL,
    "correlation_id" uuid NOT NULL,
    "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "version" bigint NOT NULL CHECK ("version" >= 0),
    "family_id" uuid NOT NULL,
    "family_session_id" uuid NOT NULL,
    "mode" varchar(4) NOT NULL,
    "rehearsal_epoch_id" uuid NULL,
    "source_id" uuid NOT NULL,
    "configuration_id" uuid NOT NULL,
    "prior_submission_id" uuid NULL,
    "form_schema" varchar(48) NOT NULL,
    "projection_version" varchar(48) NOT NULL,
    "projection_digest" varchar(64) NOT NULL,
    "definition_digest" varchar(64) NOT NULL,
    "expires_at" timestamp with time zone NOT NULL,
    "state" varchar(12) NOT NULL,
    "ended_at" timestamp with time zone NULL,
    CONSTRAINT "stewardship_responses_familyformbaseline_positive_version" CHECK ((version >= 1)),
    CONSTRAINT "family_baseline_mode_epoch" CHECK (((((mode)::text = 'live'::text) AND (rehearsal_epoch_id IS NULL)) OR (((mode)::text = 'test'::text) AND (rehearsal_epoch_id IS NOT NULL)))),
    CONSTRAINT "family_baseline_state_shape" CHECK ((((ended_at IS NULL) AND ((state)::text = 'open'::text)) OR ((ended_at >= created_at) AND (ended_at IS NOT NULL) AND ((state)::text = ANY ((ARRAY['submitted'::character varying, 'replaced'::character varying, 'cancelled'::character varying, 'expired'::character varying])::text[]))))),
    CONSTRAINT "family_baseline_future_expiry" CHECK ((expires_at > created_at)),
    CONSTRAINT "family_baseline_contract_identity" CHECK ((((projection_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((definition_digest)::text ~ '^[0-9a-f]{64}$'::text) AND (NOT ((form_schema)::text = ''::text)) AND (NOT ((projection_version)::text = ''::text))))
);
CREATE TABLE "stewardship_submission" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL,
    "correlation_id" uuid NOT NULL,
    "family_id" uuid NOT NULL,
    "campaign_id" uuid NOT NULL,
    "campaign_sequence" bigint NOT NULL CHECK ("campaign_sequence" >= 0),
    "baseline_id" uuid NOT NULL UNIQUE,
    "reviewed_source_id" uuid NOT NULL,
    "validation_source_id" uuid NOT NULL,
    "configuration_id" uuid NOT NULL,
    "prior_submission_id" uuid NULL,
    "mode" varchar(4) NOT NULL,
    "rehearsal_epoch_id" uuid NULL,
    "family_version" bigint NOT NULL CHECK ("family_version" >= 0),
    "submitted_at" timestamp with time zone NOT NULL,
    "submitted_on" date NOT NULL,
    "form_schema" varchar(48) NOT NULL,
    "answers" jsonb NOT NULL,
    "annual_pledge" numeric(14, 2) NULL,
    CONSTRAINT "submission_campaign_sequence" UNIQUE ("campaign_id", "mode", "campaign_sequence"),
    CONSTRAINT "submission_family_version" UNIQUE ("family_id", "mode", "family_version"),
    CONSTRAINT "submission_mode_epoch" CHECK (((((mode)::text = 'live'::text) AND (rehearsal_epoch_id IS NULL)) OR (((mode)::text = 'test'::text) AND (rehearsal_epoch_id IS NOT NULL)))),
    CONSTRAINT "submission_version_identity" CHECK (((campaign_sequence > 0) AND (family_version > 0) AND (NOT ((form_schema)::text = ''::text)))),
    CONSTRAINT "submission_supported_pledge" CHECK (((annual_pledge IS NULL) OR ((annual_pledge >= (0)::numeric) AND (annual_pledge <= 999999999.99)))),
    CONSTRAINT "submission_not_own_predecessor" CHECK ((NOT ((prior_submission_id = id) AND (prior_submission_id IS NOT NULL))))
);
CREATE TABLE "stewardship_proposed_change" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL,
    "correlation_id" uuid NOT NULL,
    "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "version" bigint NOT NULL CHECK ("version" >= 0),
    "submission_id" uuid NOT NULL,
    "entity_kind" varchar(20) NOT NULL,
    "entity_key" varchar(36) NOT NULL,
    "field" varchar(40) NOT NULL,
    "baseline_available" boolean NOT NULL,
    "baseline_value" jsonb NULL,
    "submitted_value" jsonb NULL,
    "current_available" boolean NOT NULL,
    "current_value" jsonb NULL,
    "current_source_id" uuid NOT NULL,
    "admin_value_set" boolean NOT NULL,
    "admin_value" jsonb NULL,
    "handling" varchar(12) NOT NULL,
    "decision" varchar(12) NOT NULL,
    "execution" varchar(20) NOT NULL,
    "superseded_by_id" uuid NULL,
    CONSTRAINT "stewardship_responses_proposedchange_positive_version" CHECK ((version >= 1)),
    CONSTRAINT "proposal_atomic_identity" UNIQUE ("submission_id", "entity_kind", "entity_key", "field"),
    CONSTRAINT "proposal_entity_shape" CHECK ((((entity_kind)::text = ANY ((ARRAY['family'::character varying, 'member'::character varying, 'proposed_member'::character varying])::text[])) AND (NOT ((entity_key)::text = ''::text)) AND (NOT ((field)::text = ''::text)))),
    CONSTRAINT "proposal_handling" CHECK (((handling)::text = ANY ((ARRAY['api'::character varying, 'manual'::character varying, 'report-only'::character varying])::text[]))),
    CONSTRAINT "proposal_decision" CHECK (((decision)::text = ANY ((ARRAY['unreviewed'::character varying, 'approved'::character varying, 'ignored'::character varying])::text[]))),
    CONSTRAINT "proposal_execution" CHECK (((execution)::text = ANY ((ARRAY['pending'::character varying, 'conflict'::character varying, 'queued'::character varying, 'published'::character varying, 'resolved_upstream'::character varying, 'resolved_external'::character varying, 'failed'::character varying, 'superseded'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT "proposal_baseline_availability" CHECK ((baseline_available OR (baseline_value IS NULL))),
    CONSTRAINT "proposal_current_availability" CHECK ((current_available OR (current_value IS NULL))),
    CONSTRAINT "proposal_admin_value_shape" CHECK ((admin_value_set OR (admin_value IS NULL))),
    CONSTRAINT "proposal_not_own_successor" CHECK ((NOT ((superseded_by_id = id) AND (superseded_by_id IS NOT NULL))))
);
CREATE TABLE "stewardship_additional_information" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL,
    "correlation_id" uuid NOT NULL,
    "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "version" bigint NOT NULL CHECK ("version" >= 0),
    "submission_id" uuid NOT NULL UNIQUE,
    "text" text NOT NULL,
    "disposition" varchar(20) NOT NULL,
    "replacement_id" uuid NULL UNIQUE,
    "follow_up_needed" boolean NOT NULL,
    "followed_up_at" timestamp with time zone NULL,
    CONSTRAINT "stewardship_responses_additionalinformationitem_positive_version" CHECK ((version >= 1)),
    CONSTRAINT "additional_nonblank_text" CHECK ((NOT (text = ''::text))),
    CONSTRAINT "additional_disposition_shape" CHECK (((((disposition)::text = 'superseded'::text) AND (replacement_id IS NOT NULL)) OR (((disposition)::text = ANY ((ARRAY['current_actionable'::character varying, 'withdrawn'::character varying])::text[])) AND (replacement_id IS NULL)))),
    CONSTRAINT "additional_not_own_replacement" CHECK ((NOT ((replacement_id = id) AND (replacement_id IS NOT NULL))))
);
CREATE TABLE "stewardship_submission_receipt" (
    "id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL,
    "correlation_id" uuid NOT NULL,
    "submission_id" uuid NOT NULL UNIQUE,
    "disposition" varchar(28) NOT NULL,
    CONSTRAINT "submission_receipt_disposition" CHECK (((disposition)::text = ANY ((ARRAY['pending_preparation'::character varying, 'no_deliverable_recipient'::character varying])::text[])))
);
CREATE TABLE "stewardship_ministry_request" ("id" uuid NOT NULL PRIMARY KEY,
    "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "actor_id" uuid NULL,
    "correlation_id" uuid NOT NULL,
    "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL,
    "version" bigint NOT NULL CHECK ("version" >= 0),
    "submission_id" uuid NOT NULL,
    "entity_kind" varchar(20) NOT NULL,
    "entity_key" varchar(36) NOT NULL,
    "ministry_duid" integer NOT NULL CHECK ("ministry_duid" >= 0),
    "action" varchar(5) NOT NULL,
    "state" varchar(20) NOT NULL,
    "outcome" varchar(20) NULL,
    "resolved_at" timestamp with time zone NULL,
    "resolution_source_id" uuid NULL,
    "superseded_by_id" uuid NULL,
    CONSTRAINT "stewardship_workflows_ministryrequest_positive_version" CHECK ("version" >= 1),
    CONSTRAINT "ministry_request_identity" UNIQUE ("submission_id",
    "entity_kind",
    "entity_key",
    "ministry_duid"),
    CONSTRAINT "ministry_request_entity" CHECK ((((entity_kind)::text = ANY ((ARRAY['member'::character varying, 'proposed_member'::character varying])::text[])) AND (NOT ((entity_key)::text = ''::text)) AND (ministry_duid > 0))),
    CONSTRAINT "ministry_request_action" CHECK ((((action)::text = ANY ((ARRAY['join'::character varying, 'leave'::character varying])::text[])) AND (NOT (((action)::text = 'leave'::text) AND ((entity_kind)::text = 'proposed_member'::text))))),
    CONSTRAINT "ministry_request_state" CHECK (((state)::text = ANY ((ARRAY['new'::character varying, 'assigned'::character varying, 'in_progress'::character varying, 'resolved'::character varying, 'closed_no_response'::character varying, 'cancelled'::character varying, 'superseded'::character varying])::text[]))),
    CONSTRAINT "ministry_request_outcome" CHECK (((((state)::text = ANY ((ARRAY['resolved'::character varying, 'closed_no_response'::character varying])::text[])) AND (outcome IS NOT NULL) AND (resolved_at IS NOT NULL) AND ((outcome)::text = ANY ((ARRAY['joined'::character varying, 'leave_confirmed'::character varying, 'declined'::character varying, 'no_response'::character varying, 'duplicate'::character varying, 'other'::character varying])::text[]))) OR ((NOT ((state)::text = ANY ((ARRAY['resolved'::character varying, 'closed_no_response'::character varying])::text[]))) AND (outcome IS NULL) AND (resolution_source_id IS NULL) AND (resolved_at IS NULL)))),
    CONSTRAINT "ministry_request_successor" CHECK ((("state" = 'superseded' AND "superseded_by_id" IS NOT NULL) OR (NOT ("state" = 'superseded') AND "superseded_by_id" IS NULL))),
    CONSTRAINT "ministry_request_not_own_next" CHECK (NOT ("superseded_by_id" = ("id") AND "superseded_by_id" IS NOT NULL)));

-- Phase 4: durable delivery journal (fresh-install baseline only).
CREATE TABLE "stewardship_delivery_pause_hold" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "pause_version" bigint NOT NULL CHECK ("pause_version" >= 0), CONSTRAINT "delivery_pause_identity" UNIQUE (campaign_id, pause_version), CONSTRAINT "delivery_pause_positive" CHECK ((pause_version >= 1)));
CREATE TABLE "stewardship_outbox_message" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "command_id" uuid NOT NULL, "command_digest" varchar(64) NOT NULL, "action" varchar(24) NOT NULL, "provider_key_digest" varchar(64) NOT NULL, "provider_message_digest" varchar(64) NOT NULL, "evidence_digest" varchar(64) NOT NULL, "evidence_note" varchar(2000) NOT NULL, "reason" varchar(64) NOT NULL, "scope_id" uuid NOT NULL, "semantic_key" uuid NOT NULL, "campaign_id" uuid NULL, "family_id" uuid NULL, "mode" varchar(16) NOT NULL, "routing" varchar(24) NOT NULL, "purpose" varchar(24) NOT NULL, "task_id" uuid NOT NULL UNIQUE, "render_id" uuid NOT NULL, "credential_namespace" varchar(16) NOT NULL, "rehearsal_epoch_id" uuid NULL, "token_generation_id" uuid NULL, "credential_epoch_id" uuid NULL, "sealed_substitutions" text NULL, "sealed_key_id" varchar(48) NULL, "state" varchar(24) DEFAULT 'pending' NOT NULL, "attempt" bigint DEFAULT 0 NOT NULL CHECK ("attempt" >= 0), "not_before" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, "submitted_at" timestamp with time zone NULL, "provider_deadline" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL, "pause_hold_id" uuid NULL, "pause_version" bigint NULL CHECK ("pause_version" >= 0));
CREATE TABLE "stewardship_outbox_render" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "message_id" uuid NOT NULL, "configuration_id" uuid NOT NULL, "template_id" uuid NULL, "sender" varchar(254) NOT NULL, "intended_recipients" jsonb NOT NULL, "routed_recipients" jsonb NOT NULL, "subject" varchar(254) NOT NULL, "html" text NOT NULL, "text" text NOT NULL, "payload_digest" varchar(64) NOT NULL, CONSTRAINT "outbox_render_digest" CHECK (((payload_digest)::text ~ '^[0-9a-f]{64}$'::text)), CONSTRAINT "outbox_render_subject" CHECK ((NOT ((subject)::text = ''::text))));
CREATE TABLE "stewardship_outbox_event" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "command_id" uuid NOT NULL, "command_digest" varchar(64) NOT NULL, "action" varchar(24) NOT NULL, "provider_key_digest" varchar(64) NOT NULL, "provider_message_digest" varchar(64) NOT NULL, "evidence_digest" varchar(64) NOT NULL, "evidence_note" varchar(2000) NOT NULL, "reason" varchar(64) NOT NULL, "message_id" uuid NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "previous_state" varchar(24) NOT NULL, "state" varchar(24) NOT NULL, "attempt" bigint NOT NULL CHECK ("attempt" >= 0), "render_id" uuid NOT NULL, "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, "not_before" timestamp with time zone NOT NULL, "submitted_at" timestamp with time zone NULL, "provider_deadline" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL, CONSTRAINT "outbox_event_version" UNIQUE (message_id, version), CONSTRAINT "outbox_command_once" UNIQUE (message_id, command_id), CONSTRAINT "outbox_event_positive" CHECK ((version >= 1)), CONSTRAINT "outbox_command_digest" CHECK (((command_digest)::text ~ '^[0-9a-f]{64}$'::text)), CONSTRAINT "outbox_event_action" CHECK (((action)::text = ANY ((ARRAY['created'::character varying, 'prepared'::character varying, 'hold'::character varying, 'release_hold'::character varying, 'submit'::character varying, 'accept'::character varying, 'retry_unaccepted'::character varying, 'fail_unaccepted'::character varying, 'mark_unknown'::character varying, 'cancel_unsent'::character varying, 'retry_failed'::character varying, 'authorize_resend'::character varying, 'retry_idempotent'::character varying])::text[]))), CONSTRAINT "outbox_event_state" CHECK (((state)::text = ANY ((ARRAY['pending'::character varying, 'submitting'::character varying, 'retry_wait'::character varying, 'delivery_unknown'::character varying, 'delivered'::character varying, 'permanent_failure'::character varying, 'cancelled'::character varying])::text[]))), CONSTRAINT "outbox_event_previous_state" CHECK (((previous_state)::text = ANY ((ARRAY[''::character varying, 'pending'::character varying, 'submitting'::character varying, 'retry_wait'::character varying, 'delivery_unknown'::character varying, 'delivered'::character varying, 'permanent_failure'::character varying, 'cancelled'::character varying])::text[]))), CONSTRAINT "outbox_event_digests" CHECK (((((provider_key_digest)::text = ''::text) OR ((provider_key_digest)::text ~ '^[0-9a-f]{64}$'::text)) AND (((provider_message_digest)::text = ''::text) OR ((provider_message_digest)::text ~ '^[0-9a-f]{64}$'::text)) AND (((evidence_digest)::text = ''::text) OR ((evidence_digest)::text ~ '^[0-9a-f]{64}$'::text)))), CONSTRAINT "outbox_event_reason" CHECK ((((reason)::text = ''::text) OR ((reason)::text ~ '^[a-z][a-z0-9_]{0,63}$'::text))));
CREATE TABLE "stewardship_testing_aggregate" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "campaign_id" uuid NOT NULL, "inventory_digest" varchar(64) NOT NULL, "readiness_digest" varchar(64) NOT NULL, "submissions" bigint NOT NULL CHECK ("submissions" >= 0), "families" bigint NOT NULL CHECK ("families" >= 0), "messages" bigint NOT NULL CHECK ("messages" >= 0), "delivered" bigint NOT NULL CHECK ("delivered" >= 0), "failed" bigint NOT NULL CHECK ("failed" >= 0), "cancelled" bigint NOT NULL CHECK ("cancelled" >= 0), CONSTRAINT "testing_aggregate_digests" CHECK ((((inventory_digest)::text ~ '^[0-9a-f]{64}$'::text) AND ((readiness_digest)::text ~ '^[0-9a-f]{64}$'::text))), CONSTRAINT "testing_aggregate_families" CHECK ((families <= submissions)), CONSTRAINT "testing_aggregate_terminal" CHECK ((messages = ((delivered + failed) + cancelled))));
CREATE TABLE "stewardship_production_request" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "campaign_id" uuid NOT NULL, "initiated_by_id" uuid NOT NULL, "request_key" uuid NOT NULL, "configuration_id" uuid NOT NULL, "aggregate_id" uuid NOT NULL UNIQUE, "inventory_digest" varchar(64) NOT NULL, "inventory_counts" jsonb NOT NULL, "inventory_total" bigint NOT NULL CHECK ("inventory_total" >= 0), "gate_version" bigint NOT NULL CHECK ("gate_version" >= 0), "invalidated_epoch_id" uuid NULL, "task_id" uuid NOT NULL UNIQUE, "acknowledged_at" timestamp with time zone NOT NULL, "reauthenticated_at" timestamp with time zone NOT NULL, "state" varchar(24) DEFAULT 'cleanup_queued' NOT NULL, "action" varchar(24) DEFAULT 'created' NOT NULL, "command_id" uuid NOT NULL, "processed_count" bigint DEFAULT 0 NOT NULL CHECK ("processed_count" >= 0), "checkpoint_sequence" bigint DEFAULT 0 NOT NULL CHECK ("checkpoint_sequence" >= 0), "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, "failure_reason" varchar(64) NOT NULL, "activated_at" timestamp with time zone NULL, "activation_digest" varchar(64) NOT NULL);
CREATE TABLE "stewardship_production_checkpoint" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL, "command_id" uuid NOT NULL, "sequence" bigint NOT NULL CHECK ("sequence" >= 0), "counts" jsonb NOT NULL, "deleted_count" bigint NOT NULL CHECK ("deleted_count" >= 0), "batch_digest" varchar(64) NOT NULL, "run_id" uuid NOT NULL, "task_fence" bigint NOT NULL CHECK ("task_fence" >= 0), "worker_id" uuid NOT NULL, CONSTRAINT "production_checkpoint_order" UNIQUE (request_id, sequence), CONSTRAINT "production_checkpoint_command" UNIQUE (request_id, command_id), CONSTRAINT "production_checkpoint_positive" CHECK (((deleted_count >= 1) AND (sequence >= 1) AND (task_fence >= 1))), CONSTRAINT "production_batch_digest" CHECK (((batch_digest)::text ~ '^[0-9a-f]{64}$'::text)));
CREATE TABLE "stewardship_production_event" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "request_id" uuid NOT NULL, "command_id" uuid NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "previous_state" varchar(24) NOT NULL, "state" varchar(24) NOT NULL, "action" varchar(24) NOT NULL, "snapshot" jsonb NOT NULL, CONSTRAINT "production_event_version" UNIQUE (request_id, version), CONSTRAINT "production_event_command" UNIQUE (request_id, command_id), CONSTRAINT "production_event_positive" CHECK ((version >= 1)), CONSTRAINT "production_event_state" CHECK (((state)::text = ANY ((ARRAY['cleanup_queued'::character varying, 'cleanup_running'::character varying, 'cleanup_retry_wait'::character varying, 'cleanup_complete'::character varying, 'cleanup_failed'::character varying, 'activated'::character varying, 'cancelled'::character varying])::text[]))));
