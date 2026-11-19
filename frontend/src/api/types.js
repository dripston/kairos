// JSDoc types for the Kairos backend responses this UI reads. Shapes follow backend/kairos_backend/api.py on
// main (K06), k06/hitl-control, k26/report-pack, k17-k18/voice-engine and k07-k08/channel-adapters.
// Money is integer paise. Every response is SIMULATED unless it says otherwise.

/** @typedef {'LIVE SANDBOX' | 'REPLAY' | 'SIMULATED'} Provenance */
/** @typedef {'live' | 'mock' | 'loading'} Source */

/**
 * @typedef {Object} Health
 * @property {boolean} ok
 * @property {string} policy_version
 * @property {string} env  e.g. "SIMULATED"
 */

/**
 * @typedef {Object} AuditRow  GET /audit, GET /hitl/audit
 * @property {number} id
 * @property {string} ts ISO time with offset
 * @property {string} actor e.g. supervisor | executor | provider | borrower | agent:voice | human:ops
 * @property {string} type e.g. DECISION | SEND_TIME_CHECK | RESPONSE | VOICE_DISPOSITION | PROVIDER_STATUS
 * @property {?string} borrower_id
 * @property {?string} instruction_id
 * @property {?('SEND'|'WAIT'|'CANCEL'|'HANDOFF')} decision
 * @property {?string} reason
 * @property {?string} rule
 * @property {string} policy_version
 * @property {Object} detail
 */

/**
 * @typedef {Object} CampaignState  GET /campaigns/{id}
 * @property {string} id
 * @property {string} name
 * @property {string} status
 * @property {number} budget_per_case_paise
 * @property {number} allocated_cases
 * @property {number} pool_paise
 * @property {number} committed_paise
 * @property {number} collected_paise
 * @property {number} cured_count
 * @property {boolean} target_met
 * @property {{day: string, allowance_paise: number, committed_paise: number}[]} days
 */

/**
 * @typedef {Object} CapBreaches  GET /cap-breaches
 * @property {number} count
 * @property {Object[]} rows
 */

/**
 * @typedef {Object} Obligation
 * @property {string} obligation_id
 * @property {number} installment_no
 * @property {string} due_date
 * @property {number} amount_due_paise
 * @property {number} amount_paid_paise
 * @property {number} outstanding_paise
 */

/**
 * @typedef {Object} BorrowerState  GET /borrowers/{id} (phone is masked by the client before it reaches a page)
 * @property {string} borrower_id
 * @property {string} full_name
 * @property {string} phone  masked
 * @property {string} language
 * @property {Object} state  K06 holds: stopped, hardship_until, callback_pending, supervisor_hold, ptp_date …
 * @property {{channel: string, consented: number, opted_out: number}[]} consents
 * @property {{loan_id: string, product: string, dpd: ?number, band: ?string, cured: boolean, obligations: Obligation[]}[]} loans
 * @property {Object[]} touches
 * @property {Object[]} tickets
 * @property {Object[]} events
 */

/**
 * @typedef {Object} Approval  GET /hitl/approvals
 * @property {string} instruction_id
 * @property {string} borrower_id
 * @property {string} loan_id
 * @property {string} channel
 * @property {string} template_id
 * @property {string} scheduled_at
 * @property {string} status PENDING_APPROVAL | …
 * @property {string[]} rules  why a person must approve (VOICE_CHANNEL, STANDARD_INTENSITY_HIGH_DPD, MANUAL_DISPATCH)
 * @property {number} est_cost_paise
 * @property {string} expires_at
 * @property {string} phone  masked by the backend
 */

/**
 * @typedef {Object} BorrowerControls  GET /hitl/borrowers/{id}
 * @property {Object[]} holds
 * @property {boolean} supervisor_hold
 * @property {boolean} stopped
 * @property {?Object} dnc
 * @property {string[]} allowed_actions  for the caller's role
 * @property {string[]} locked_by_guardrails
 */

/**
 * @typedef {Object} ManualPreview  POST /hitl/manual/preview
 * @property {boolean} allowed
 * @property {string} reason
 * @property {{decision: string, reason: string, rule: string, policy_version: string, band: string}} gate
 * @property {string[]} would_need_approval
 * @property {{template_id: string, rendered: string, intensity: string}} preview
 */

/**
 * @typedef {Object} DailySegment  GET /reports/daily-segment
 * @property {'SIMULATED'} provenance
 * @property {string} day
 * @property {string} policy_version
 * @property {{band: string, channel: string, attempts: number, reach: number, meaningful_replies: number,
 *   spend_committed_inr: number, replies_per_1000_inr: number, cap_breaches: number}[]} segments
 * @property {Object} totals
 * @property {{available: boolean, arms: {band: string, channel: string, slot: string, posterior_mean: number, n: number}[],
 *   decision_share: Object[], decisions_today: number}} learning
 * @property {string[]} notes
 */

/**
 * @typedef {Object} VoiceSelection  POST /voice/select
 * @property {?string} use_case
 * @property {?string} agent_id
 * @property {?string} voice  Ritu | Advait
 * @property {string} language
 * @property {string} reason
 * @property {boolean} ready
 * @property {boolean} requires_human_approval
 */

/**
 * @typedef {Object} DemoRecipients  GET/POST/DELETE /demo/recipients  — numbers are always masked
 * @property {string[]} recipients
 * @property {string} [note]
 */

export {}
