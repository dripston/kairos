// Synthetic data for the Kairos demo. Deterministic: same seed, same data.

export function rng(seed) {
  let s = seed >>> 0
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 4294967296
  }
}
const pick = (r, arr) => arr[Math.floor(r() * arr.length)]

export const CAMPAIGN_DAYS = 10
export const START_DAY = 3
export const HERO_ID = 'B-1042'

export const CHANNELS = ['sms', 'whatsapp', 'email', 'ivr', 'voicebot']
export const CHANNEL_META = {
  sms: { label: 'SMS', cost: 0.2, color: '#f97316' },
  whatsapp: { label: 'WhatsApp', cost: 0.4, color: '#22c55e' },
  email: { label: 'Email', cost: 0.05, color: '#a78bfa' },
  ivr: { label: 'IVR', cost: 1.2, color: '#3b82f6' },
  voicebot: { label: 'Voicebot', cost: 2.5, color: '#eab308' },
}

export const BANDS = ['1–29', '30–59', '60–89', '90+']
export const band = (dpd) => (dpd < 30 ? '1–29' : dpd < 60 ? '30–59' : dpd < 90 ? '60–89' : '90+')

export const TEMPLATES = [
  { id: 'SMS_REMIND_HI_01', channel: 'sms', lang: 'Hindi', intensity: 'LOW', purpose: 'Reminder' },
  { id: 'SMS_REMIND_EN_01', channel: 'sms', lang: 'English', intensity: 'LOW', purpose: 'Reminder' },
  { id: 'SMS_DUE_HI_02', channel: 'sms', lang: 'Hindi', intensity: 'STANDARD', purpose: 'Overdue notice' },
  { id: 'WA_REMIND_HI_SOFT_02', channel: 'whatsapp', lang: 'Hindi', intensity: 'LOW', purpose: 'Soft reminder' },
  { id: 'WA_PAYLINK_HI_01', channel: 'whatsapp', lang: 'Hindi', intensity: 'STANDARD', purpose: 'Pay link' },
  { id: 'WA_PAYLINK_EN_01', channel: 'whatsapp', lang: 'English', intensity: 'STANDARD', purpose: 'Pay link' },
  { id: 'EM_STATEMENT_EN_01', channel: 'email', lang: 'English', intensity: 'LOW', purpose: 'Statement' },
  { id: 'IVR_DUE_HI_01', channel: 'ivr', lang: 'Hindi', intensity: 'STANDARD', purpose: 'Due reminder call' },
  { id: 'IVR_DUE_EN_01', channel: 'ivr', lang: 'English', intensity: 'STANDARD', purpose: 'Due reminder call' },
  { id: 'VB_CALLBACK_HI_01', channel: 'voicebot', lang: 'Hindi', intensity: 'LOW', purpose: 'Consented callback' },
]

const FIRST = ['Aarav', 'Priya', 'Rohan', 'Sneha', 'Vikram', 'Ananya', 'Karan', 'Meera', 'Arjun', 'Pooja', 'Rahul', 'Divya', 'Imran', 'Fatima', 'Suresh', 'Lakshmi', 'Manoj', 'Kavya', 'Nikhil', 'Ritu', 'Sanjay', 'Neha', 'Farhan', 'Asha']
const LAST = ['Sharma', 'Patel', 'Rao', 'Iyer', 'Khan', 'Gupta', 'Nair', 'Das', 'Joshi', 'Reddy', 'Singh', 'Mehta']
const REGIONS = ['Mumbai', 'Pune', 'Delhi', 'Bengaluru', 'Chennai', 'Hyderabad', 'Kolkata', 'Jaipur', 'Lucknow', 'Ahmedabad']
const LANGS = ['Hindi', 'Hindi', 'Hindi', 'English', 'English', 'Marathi', 'Tamil', 'Telugu', 'Kannada', 'Bengali']

// ---------- Smart CRM accounts ----------
export function makeBorrowers(n = 500, seed = 42) {
  const r = rng(seed)
  const list = []
  for (let i = 1; i <= n; i++) {
    const id = 'B-' + String(i).padStart(4, '0')
    const dpd = Math.max(1, Math.round(r() < 0.45 ? r() * 29 : r() < 0.7 ? 30 + r() * 29 : 60 + r() * 45))
    const nLoans = r() < 0.25 ? 2 : 1
    const loans = Array.from({ length: nLoans }, (_, k) => {
      const emi = Math.round((1500 + r() * 9000) / 50) * 50
      return {
        id: 'PL-' + (80000 + Math.floor(r() * 19999)),
        label: k === 0 ? 'A' : 'B',
        emi,
        outstanding: Math.round(emi * (6 + r() * 18) / 100) * 100,
        dpd,
        status: 'OPEN',
      }
    })
    const roll = r()
    const flags = {
      stop: roll < 0.03,
      hardship: roll >= 0.03 && roll < 0.05,
      hold: roll >= 0.05 && roll < 0.11 ? { until: 3 + Math.ceil(r() * 4), reason: 'Promise to pay' } : null,
      callback: roll >= 0.11 && roll < 0.13,
      paid: roll >= 0.13 && roll < 0.2,
    }
    if (flags.paid) loans.forEach((l) => (l.status = 'PAID'))
    const consent = {
      sms: r() > 0.04,
      whatsapp: r() > 0.18,
      email: r() > 0.35,
      ivr: r() > 0.22,
      voicebot: r() > 0.7,
    }
    const lang = pick(r, LANGS)
    const name = `${pick(r, FIRST)} ${pick(r, LAST)}`
    let nextAction = 'SEND'
    if (flags.stop) nextAction = 'STOP'
    else if (flags.hardship) nextAction = 'HANDOFF'
    else if (flags.paid) nextAction = 'DONE'
    else if (flags.hold) nextAction = 'WAIT'
    else if (r() < 0.35) nextAction = 'WAIT'
    const reachable = CHANNELS.filter((c) => consent[c] && c !== 'voicebot')
    list.push({
      id,
      name,
      phone: `+91 9${Math.floor(r() * 9)}•••• ${String(1000 + Math.floor(r() * 8999))}`,
      language: lang,
      region: pick(r, REGIONS),
      dpd,
      band: band(dpd),
      loans,
      consent,
      consentVersion: { sms: 'v2', whatsapp: 'v3', email: 'v1', ivr: 'v2', voicebot: 'v1' },
      flags,
      capsUsed: Math.floor(r() * 3),
      capLimit: 3,
      nextAction,
      nextChannel: reachable.length ? pick(r, reachable) : null,
      lastTouchDay: Math.max(1, START_DAY - Math.floor(r() * 3)),
      lastTouchChannel: reachable.length ? pick(r, reachable) : 'sms',
      spend: Math.round(r() * 160) / 100,
      campaignId: 'C-01',
      hero: false,
    })
  }
  return list
}

export function makeHero() {
  return {
    id: HERO_ID,
    name: 'Ravi Kumar',
    phone: '+91 98•••• 4417',
    language: 'Hindi',
    region: 'Pune',
    dpd: 42,
    band: band(42),
    loans: [
      { id: 'PL-88231', label: 'A', emi: 4850, outstanding: 62400, dpd: 42, status: 'OPEN' },
      { id: 'PL-90412', label: 'B', emi: 3200, outstanding: 41900, dpd: 42, status: 'OPEN' },
    ],
    consent: { sms: true, whatsapp: true, email: false, ivr: true, voicebot: false },
    consentVersion: { sms: 'v2', whatsapp: 'v3', email: 'v1 · hard bounce', ivr: 'v2', voicebot: 'not given' },
    flags: { stop: false, hardship: false, hold: { until: 4, reason: 'Promise to pay (Loan A)' }, callback: false, paid: false },
    capsUsed: 2,
    capLimit: 3,
    nextAction: 'WAIT',
    nextChannel: 'whatsapp',
    lastTouchDay: 3,
    lastTouchChannel: 'email',
    spend: 0.65,
    campaignId: 'C-01',
    hero: true,
    payments: [],
    cases: [],
    otp: 'NOT_STARTED',
    agents: {
      summary: [
        'SMS delivered on Day 1 (read unknown). WhatsApp read on Day 2 evening; email bounced on Day 3.',
        'Borrower replied "Kal pay kar dunga" — promise to pay Loan A. No verified payment yet.',
      ],
      channel: { best: 'whatsapp', window: '18:00–20:00', alts: ['SMS · 10:00', 'IVR · 11:00'], evidence: 'Strong (2 evening reads)' },
      propensity: { p: 0.62, horizon: '7 days', intensity: 'LOW' },
      template: { id: 'WA_REMIND_HI_SOFT_02', lang: 'Hindi' },
    },
    supervisor: {
      action: 'WAIT',
      reason: 'Promise-to-pay hold is active until Day 4 09:00.',
      version: 4,
      expires: 'Day 4 · 09:00',
      checks: [
        { label: 'Consent (WhatsApp v3)', ok: true },
        { label: 'Cap 2 of 3 this week', ok: true },
        { label: 'Budget available', ok: true },
        { label: 'Quiet hours', ok: true },
        { label: 'Approved template', ok: true },
        { label: 'No active hold', ok: false },
      ],
    },
  }
}

// ---------- Campaigns ----------
export function makeCampaigns() {
  return [
    {
      id: 'C-01', name: 'Mid-DPD recovery · November', purpose: 'Recover 1–89 DPD personal-loan EMIs at the lowest cost per reply.',
      status: 'RUNNING', source: 'CRM', enrolled: 487, excluded: 13, mode: 'ADAPTIVE',
      channels: { sms: true, whatsapp: true, email: true, ivr: true, voicebot: false },
      dailyBudget: 150, totalBudget: 1500, startDay: 1, endDay: 10, caps: { day: 1, week: 3 }, frozen: false,
    },
    {
      id: 'C-02', name: 'Fixed-cadence baseline', purpose: 'Same audience, budget and caps on the official fixed cadence — the benchmark.',
      status: 'RUNNING', source: 'CRM', enrolled: 487, excluded: 13, mode: 'FIXED',
      channels: { sms: true, whatsapp: true, email: true, ivr: false, voicebot: false },
      dailyBudget: 150, totalBudget: 1500, startDay: 1, endDay: 10, caps: { day: 1, week: 3 }, frozen: false,
    },
    {
      id: 'C-03', name: 'Early-bucket nudges', purpose: 'Light-touch reminders for 1–15 DPD before they roll into the next band.',
      status: 'SCHEDULED', source: 'CRM', enrolled: 212, excluded: 6, mode: 'ADAPTIVE',
      channels: { sms: true, whatsapp: true, email: false, ivr: false, voicebot: false },
      dailyBudget: 60, totalBudget: 600, startDay: 5, endDay: 14, caps: { day: 1, week: 2 }, frozen: false,
    },
    {
      id: 'C-04', name: '60+ DPD soft calls', purpose: 'Consented IVR follow-ups for late buckets — paused for policy review.',
      status: 'PAUSED', source: 'CSV', enrolled: 107, excluded: 9, mode: 'SHADOW',
      channels: { sms: false, whatsapp: true, email: false, ivr: true, voicebot: true },
      dailyBudget: 80, totalBudget: 800, startDay: 2, endDay: 12, caps: { day: 1, week: 2 }, frozen: false,
    },
    {
      id: 'C-05', name: 'Pre-due reminders · December', purpose: 'Remind current customers 3 days before the EMI date.',
      status: 'DRAFT', source: 'CRM', enrolled: 0, excluded: 0, mode: 'ADAPTIVE',
      channels: { sms: true, whatsapp: true, email: true, ivr: false, voicebot: false },
      dailyBudget: 100, totalBudget: 1000, startDay: 12, endDay: 20, caps: { day: 1, week: 2 }, frozen: false,
    },
  ]
}

// ---------- Learning series (labelled SIMULATED in the UI) ----------
// reply-probability estimates per channel, Day 1..10
export const ESTIMATES = {
  whatsapp: [0.18, 0.18, 0.17, 0.15, 0.13, 0.12, 0.12, 0.12, 0.118, 0.118],
  sms: [0.07, 0.072, 0.075, 0.078, 0.08, 0.08, 0.081, 0.082, 0.082, 0.083],
  email: [0.012, 0.012, 0.011, 0.011, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01],
  ivr: [0.22, 0.21, 0.21, 0.2, 0.2, 0.19, 0.19, 0.19, 0.19, 0.19],
}
export const UNCERTAINTY = [0.09, 0.07, 0.055, 0.045, 0.037, 0.031, 0.027, 0.024, 0.022, 0.02]
export const SWITCH_DAY = 4

// per-day campaign totals: adaptive (Kairos) vs fixed cadence, same seed/budget/caps
export const SERIES = {
  adaptive: {
    spend: [148, 141, 136, 131, 127, 124, 122, 121, 120, 119],
    replies: [44, 46, 47, 48, 49, 49, 50, 50, 50, 50],
    payments: [6, 7, 8, 8, 9, 9, 9, 10, 10, 10],
    reach: [212, 268, 301, 327, 349, 366, 380, 391, 399, 405],
  },
  fixed: {
    spend: [150, 150, 150, 150, 150, 150, 150, 150, 150, 150],
    replies: [44, 43, 42, 41, 40, 39, 38, 38, 37, 37],
    payments: [6, 6, 6, 7, 7, 7, 7, 7, 7, 7],
    reach: [230, 280, 309, 328, 342, 352, 359, 364, 368, 371],
  },
}

export const SEGMENT_SHARE = { '1–29': 0.42, '30–59': 0.33, '60–89': 0.19, '90+': 0.06 }
export const SEGMENT_RPR_FACTOR = { '1–29': 1.18, '30–59': 1.0, '60–89': 0.78, '90+': 0.55 }

export const sumTo = (arr, day) => arr.slice(0, day).reduce((a, b) => a + b, 0)
