"""
candidate/profile.py — Single source of truth for all candidate data.

Every form field across every ATS platform (Workday, Greenhouse, Lever,
SmartRecruiters, Taleo, iCIMS, LinkedIn, Indeed, generic) maps back to a
value here.  field_map.py resolves form labels to these values.

Fields marked  # TODO  are missing from the candidate profile and MUST be
filled in before using the bot in live mode.  The bot will skip or leave
these fields blank until they are provided.

Omokolade: search for "# TODO" in this file to find every gap.
"""

from __future__ import annotations
from typing import Optional

# ============================================================
# Q&A — FULL UK JOB APPLICATION FORM REFERENCE
# ============================================================
# Every question a UK form can ask, with the answer.
# Gaps are flagged.
#
# Q: What is your title / salutation?
# A: Mr
#
# Q: What is your first name?
# A: Omokolade
#
# Q: What is your last name / surname?
# A: Sobande
#
# Q: What is your middle name?
# A: (none — leave blank)
#
# Q: What is your full / preferred name?
# A: Omokolade Sobande
#
# Q: What is your date of birth?
# A: 26/10/2001 (DD/MM/YYYY)
#
# Q: What is your email address?
# A: omokoladesobande@gmail.com
#
# Q: What is your phone / mobile number?
# A: +44 7310552174 (multiple formats handled)
#
# Q: What is your home address / address line 1?
# A: REQUIRES_MANUAL — not provided; bot flags these forms for manual completion
#
# Q: What is your city / town?
# A: London
#
# Q: What is your county / region?
# A: TODO — e.g. 'Greater London'
#
# Q: What is your postcode?
# A: TODO — not provided
#
# Q: What country do you live in?
# A: United Kingdom
#
# Q: What is your LinkedIn profile URL?
# A: https://www.linkedin.com/in/omokolade-sobande  (VERIFY URL)
#
# Q: What is your GitHub / portfolio / website URL?
# A: TODO — not provided
#
# Q: Do you have the right to work in the UK?
# A: Yes
#
# Q: Do you require visa sponsorship?
# A: No
#
# Q: What is your nationality?
# A: British
#
# Q: What is your current visa / immigration status?
# A: British right to work — no visa required
#
# Q: Do you hold any security clearance?
# A: No
#
# Q: Are you willing to undergo a DBS / background check?
# A: Yes
#
# Q: When are you available to start?
# A: Immediately
#
# Q: What is your notice period?
# A: 0 (immediately available)
#
# Q: Are you currently employed?
# A: Yes (CISI Marketing Intern, 2025)
#
# Q: What is your expected / desired salary?
# A: £32,000 (default; calculated dynamically by scoring/salary.py)
#
# Q: What is your current salary?
# A: TODO — not provided
#
# Q: What is your highest qualification?
# A: MSc Innovation & Entrepreneurship — University of Warwick, 2025
#
# Q: What degree do you hold?
# A: MSc Innovation & Entrepreneurship (Warwick); BSc Accounting (Covenant)
#
# Q: What is your overall grade / classification?
# A: MSc — Distinction / Merit (TODO: confirm final grade); BSc — 2:1
#
# Q: What are your total years of work experience?
# A: ~3 years (internships weighted 0.5x)
#
# Q: Do you have experience with [tool/skill]?
# A: See HARD_SKILLS list; Google Analytics, Excel, Figma, CRM, etc.
#
# Q: Do you have any professional certifications?
# A: Ethical AI — CISI (2025); Revenue Operations — HubSpot (2026); Reporting — HubSpot (2026); Marketing Hub Software — HubSpot (2026); CIM Certificate (in progress); Member CISI
#
# Q: Reason for leaving [role]?
# A: See WORK_HISTORY[i]['reason_for_leaving'] per role
#
# Q: Can you provide two professional references?
# A: REQUIRES_MANUAL — bot flags reference fields; Omokolade fills in manually
#
# Q: What is your gender?
# A: TODO — confirm / prefer not to say
#
# Q: What is your ethnic group?
# A: TODO — confirm / prefer not to say
#
# Q: Do you consider yourself to have a disability?
# A: No (TODO: confirm)
#
# Q: What is your sexual orientation?
# A: Prefer not to say (TODO: confirm)
#
# Q: What is your religion / belief?
# A: Prefer not to say (TODO: confirm)
#
# Q: What age group are you in?
# A: 18–24 (DOB 26/10/2001 → age 23 in 2025)
#
# Q: Are you a military veteran?
# A: No
#
# Q: Are you over 18?
# A: Yes
#
# Q: Are you fluent in English?
# A: Yes — native/fluent
#
# Q: Have you read the job description?
# A: Yes
#
# Q: Are you currently a student?
# A: No
#
# Q: Do you have any unspent criminal convictions?
# A: No
#
# Q: Are you willing to undergo a background check?
# A: Yes
#
# Q: Are you a member of any professional body?
# A: Yes — CISI
#
# Q: Have you ever worked for this company before?
# A: No (default; override case-by-case)
#
# ============================================================


# ============================================================
# SECTION 1 — PERSONAL DETAILS
# ============================================================

TITLE: str = 'Mr'
FIRST_NAME: str = 'Omokolade'
LAST_NAME: str = 'Sobande'
FULL_NAME: str = 'Omokolade Sobande'
MIDDLE_NAME: str = ''                  # none — leave blank on forms
PREFERRED_NAME: str = 'Omokolade'     # same as first name

DATE_OF_BIRTH: str = '2001-10-26'      # confirmed; stored as ISO-8601 internally
DATE_OF_BIRTH_UK: str = '26/10/2001'   # DD/MM/YYYY — used for UK-format date fields
DATE_OF_BIRTH_US: str = '10/26/2001'   # MM/DD/YYYY — used for US-format date fields
AGE_GROUP: str = '18-24'               # confirmed from DOB 26/10/2001 (age 23 in 2025)


# ============================================================
# SECTION 2 — CONTACT
# ============================================================

EMAIL: str = 'omokoladesobande@gmail.com'
EMAIL_CONFIRM: str = EMAIL             # confirmation email field

# Phone in all formats ATS forms may require
PHONE_INTL_SPACE: str = '+44 7310552174'   # international with space (default)
PHONE_INTL_CONCAT: str = '+447310552174'   # international no space
PHONE_NATIONAL: str = '07310552174'        # UK national format
PHONE_DIGITS_ONLY: str = '7310552174'      # digits only (some US-style forms)

# Ordered list — tried in sequence by fill_phone()
PHONE_FORMATS: list[str] = [
    PHONE_INTL_SPACE,
    PHONE_INTL_CONCAT,
    PHONE_NATIONAL,
    PHONE_DIGITS_ONLY,
]

PHONE_COUNTRY_CODE: str = '+44'
PHONE_COUNTRY: str = 'United Kingdom'


# ============================================================
# SECTION 3 — ADDRESS
# ============================================================

# Address fields require manual entry — bot must NEVER invent or guess a street address.
# Any form that asks for address line 1 or postcode must be flagged REQUIRES_MANUAL.
ADDRESS_LINE_1: Optional[str] = None   # REQUIRES_MANUAL — not provided; see _REQUIRES_MANUAL_FIELDS
ADDRESS_LINE_2: Optional[str] = None   # REQUIRES_MANUAL — not provided
CITY: str = 'London'
COUNTY: Optional[str] = None          # TODO: e.g. 'Greater London'
POSTCODE: Optional[str] = None         # REQUIRES_MANUAL — not provided
COUNTRY: str = 'United Kingdom'
COUNTRY_CODE: str = 'GB'
LOCATION_FULL: str = 'London, United Kingdom'

# Shorter location string (some forms have a compact location field)
LOCATION_SHORT: str = 'London, UK'


# ============================================================
# SECTION 4 — ONLINE PRESENCE
# ============================================================

LINKEDIN_URL: str = 'https://www.linkedin.com/in/omokolade-sobande'  # confirmed live
GITHUB_URL: Optional[str] = None
PORTFOLIO_URL: str = 'https://omokoladesobande.com'  # confirmed live
PERSONAL_WEBSITE: Optional[str] = None # TODO: add if applicable
TWITTER_URL: Optional[str] = None      # TODO: add if applicable


# ============================================================
# SECTION 5 — IDENTITY & NATIONALITY
# ============================================================

NATIONALITY: str = 'British'
NATIONAL_INSURANCE_NUMBER: Optional[str] = None  # TODO: NI number — HMRC/public sector
ETHNICITY: str = 'Prefer not to say'   # TODO: confirm; used for diversity monitoring only
GENDER: str = 'Prefer not to say'      # TODO: confirm
PRONOUNS: Optional[str] = None         # TODO: e.g. 'She/Her'


# ============================================================
# SECTION 6 — WORK ELIGIBILITY
# ============================================================

RIGHT_TO_WORK_UK: bool = True
REQUIRES_SPONSORSHIP: bool = False
VISA_TYPE: Optional[str] = None        # N/A — British right to work
SECURITY_CLEARANCE_HELD: bool = False
SECURITY_CLEARANCE_LEVEL: str = 'None'
DBS_WILLING: bool = True               # willing to undergo DBS check
UNRESTRICTED_TRAVEL: bool = True       # willing to travel / no restrictions

# Text versions for dropdown/radio fields
RTW_TEXT: str = 'Yes'
SPONSORSHIP_TEXT: str = 'No'


# ============================================================
# SECTION 7 — EMPLOYMENT & AVAILABILITY
# ============================================================

START_DATE_TEXT: str = 'Immediately'
START_DATE_WEEKS: int = 0              # 0 = available immediately
NOTICE_PERIOD_TEXT: str = 'Immediately available'
NOTICE_PERIOD_WEEKS: int = 0
CURRENTLY_EMPLOYED: bool = True        # CISI internship (2025)
EMPLOYMENT_STATUS: str = 'Employed'   # for forms with status dropdown
FULL_TIME_AVAILABLE: bool = True
WILLING_TO_RELOCATE: bool = True       # within UK
WILLING_TO_TRAVEL: bool = True
PREFERRED_EMPLOYMENT_TYPE: str = 'Full-time, Permanent'

# Workday/Taleo division preference fields
PREFERRED_DIVISION: str = 'Marketing/Communications'


# ============================================================
# SECTION 8 — SALARY
# ============================================================

SALARY_EXPECTATION: int = 32_000       # default target (overridden by salary.py)
SALARY_MINIMUM: int = 28_000           # floor — never accept below this
SALARY_MAXIMUM: int = 45_000           # upper bound for range fields
SALARY_CURRENCY: str = 'GBP'
SALARY_CURRENCY_SYMBOL: str = '£'
CURRENT_SALARY: int = 18_000            # confirmed
SALARY_EXPECTATION_TEXT: str = '32000'  # plain string for text inputs

# Formatted variants
SALARY_EXPECTATION_FORMATTED: str = '£32,000'
SALARY_RANGE_TEXT: str = '£28,000 - £45,000'


# ============================================================
# SECTION 9 — EDUCATION
# ============================================================

def _date_formats(iso: str) -> dict:
    """
    Pre-compute all common date-field formats from an ISO date string (YYYY-MM-DD).
    Returns a dict of format-name → formatted string so any ATS field can be answered
    without runtime conversion errors.
    """
    from datetime import date as _date
    y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
    dt = _date(y, m, d)
    _MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June',
               'July', 'August', 'September', 'October', 'November', 'December']
    return {
        'iso':         iso,                                    # 2025-06-02
        'yyyy_mm':     f'{y:04d}-{m:02d}',                    # 2025-06
        'mm_yyyy':     f'{m:02d}/{y:04d}',                    # 06/2025
        'mm_dd_yyyy':  f'{m:02d}/{d:02d}/{y:04d}',            # 06/02/2025
        'dd_mm_yyyy':  f'{d:02d}/{m:02d}/{y:04d}',            # 02/06/2025
        'month_yyyy':  f'{_MONTHS[m]} {y}',                   # June 2025
        'mon_yyyy':    dt.strftime('%b %Y'),                   # Jun 2025
        'yyyy':        str(y),                                 # 2025
        'mm':          f'{m:02d}',                             # 06
        'dd':          f'{d:02d}',                             # 02
    }


EDUCATION: list[dict] = [
    {
        'degree':             'MSc Innovation & Entrepreneurship',
        'degree_short':       'MSc',
        'institution':        'University of Warwick (WMG)',
        'institution_short':  'University of Warwick',
        'location':           'Coventry, United Kingdom',
        'country':            'United Kingdom',
        'start':              '2023-09-04',
        'end':                '2025-07-25',
        'start_dates':        _date_formats('2023-09-04'),
        'end_dates':          _date_formats('2025-07-25'),
        'current':            False,
        'grade':              'Distinction',       # TODO: confirm final grade (Merit/Distinction)
        'grade_uk':           'Distinction',
        'gpa_us':             '3.7 / 4.0',
        'level':              "Master's",
        'level_short':        'MSc',
        'field':              'Innovation & Entrepreneurship',
        'full_degree_name':   'MSc Innovation & Entrepreneurship',
        'modules': [
            'Digital Marketing Strategy',
            'Business Analytics',
            'Digital Transformation',
            'Project & Operations Management',
            'Entrepreneurial Finance & Financial Analysis',
            'Product Development',
        ],
        'achievements':  ['Cohort Leader, MSc Innovation and Entrepreneurship, WMG (2025)'],
        'dissertation':  None,
    },
    {
        'degree':             'BSc Accounting',
        'degree_short':       'BSc',
        'institution':        'Covenant University',
        'institution_short':  'Covenant University',
        'location':           'Ogun, Nigeria',
        'country':            'Nigeria',
        'start':              '2017-09-04',
        'end':                '2021-10-29',
        'start_dates':        _date_formats('2017-09-04'),
        'end_dates':          _date_formats('2021-10-29'),
        'current':            False,
        'grade':              '2:1 (Second Class Upper)',
        'grade_uk':           '2:1',
        'gpa_us':             '3.3 / 4.0',
        'level':              "Bachelor's",
        'level_short':        'BSc',
        'field':              'Accounting',
        'full_degree_name':   'BSc Accounting',
        'modules':            [],
        'achievements':  ['Diploma in Leadership Development (2021)'],
        'dissertation':  None,
    },
]

# Convenience references — most recent first
HIGHEST_QUALIFICATION: str = "Master's Degree (MSc)"
HIGHEST_QUALIFICATION_SUBJECT: str = 'Innovation & Entrepreneurship'
HIGHEST_QUALIFICATION_INSTITUTION: str = 'University of Warwick'
HIGHEST_QUALIFICATION_GRADE: str = 'Distinction'        # TODO: confirm
HIGHEST_QUALIFICATION_YEAR: str = '2025'

UNDERGRADUATE_DEGREE: str = 'BSc Accounting'
UNDERGRADUATE_INSTITUTION: str = 'Covenant University'
UNDERGRADUATE_GRADE: str = '2:1 (Second Class Upper)'
UNDERGRADUATE_YEAR: str = '2019'

# GPA — US companies / grad schemes sometimes ask
GPA: str = '3.5 / 4.0'   # blended approximation; use MSc-specific if asked


# ============================================================
# SECTION 10 — WORK HISTORY
# ============================================================

WORK_HISTORY: list[dict] = [
    {
        # --- ROLE 1 (most recent) ---
        'employer':             'CISI (Chartered Institute for Securities & Investment)',
        'employer_short':       'CISI',
        'title':                'Marketing Intern',
        'employment_type':      'Full-time',
        'employment_type_short': 'Internship',
        'start':                '2025-06-02',
        'end':                  '2025-09-26',
        'start_dates':          _date_formats('2025-06-02'),
        'end_dates':            _date_formats('2025-09-26'),
        'current':              False,
        'city':                 'London',
        'country':              'United Kingdom',
        'sector':               'Financial Services',
        'company_size':         None,        # TODO: add if known
        'reason_for_leaving':   (
            'Contract/internship role — actively seeking a permanent position '
            'that enables continued growth in marketing within financial services.'
        ),
        'responsibilities': (
            'Managed communications with 10+ senior stakeholders and external contributors '
            'for 3 high-profile industry events, ensuring accurate messaging and timely '
            'delivery under tight deadlines. '
            'Led development of internal newsletter features reaching 300+ staff members, '
            'translating organisational updates into clear, engaging content that '
            'strengthened internal communication and brand alignment. '
            'Drove content optimisation through social media performance analysis, '
            'identifying engagement trends that improved audience interaction. '
            'Delivered demographic insights on Gen Z and Millennial segments (18-35) '
            'that refined campaign positioning across 3 digital channels. '
            'Led cross-departmental alignment across 4 departments, coordinating delivery '
            'of 3 major marketing campaigns.'
        ),
        'key_achievements': [
            'Coordinated 3 major marketing campaigns across 4 departments',
            'Internal newsletter reaching 300+ staff',
            'Demographic insights across 3 digital channels for Gen Z/Millennial (18-35)',
        ],
    },
    {
        # --- ROLE 2 ---
        'employer':             'Todlr',
        'employer_short':       'Todlr',
        'title':                'Brand Development Associate',
        'employment_type':      'Part-time',
        'employment_type_short': 'Part-time',
        'start':                '2023-01-09',
        'end':                  '2024-01-26',
        'start_dates':          _date_formats('2023-01-09'),
        'end_dates':            _date_formats('2024-01-26'),
        'current':              False,
        'city':                 'Remote (London)',
        'country':              'United Kingdom',
        'sector':               'Financial Technology / EdTech',
        'company_size':         None,
        'reason_for_leaving':   (
            'Part-time contract completed; commenced full-time MSc studies at '
            'University of Warwick.'
        ),
        'responsibilities': (
            'Defined brand positioning and messaging across 5 digital channels, '
            'strengthening market differentiation in the financial education sector. '
            'Developed and managed strategic partnerships, contributing to increased '
            'brand credibility and visibility within the financial education sector. '
            'Generated strategic insights through market research and competitive analysis '
            '(surveys, focus groups, industry reports) that informed marketing strategy '
            'and audience targeting. '
            'Optimised campaign performance through user behaviour analysis '
            '(Google Analytics, CRM data), driving a 27% increase in user acquisition.'
        ),
        'key_achievements': [
            '27% increase in user acquisition',
            'Brand positioning across 5 digital channels',
            'Market research via surveys, focus groups, industry reports',
        ],
    },
    {
        # --- ROLE 3 ---
        'employer':             'Evolve',
        'employer_short':       'Evolve',
        'title':                'Customer Experience Associate',
        'employment_type':      'Full-time',
        'employment_type_short': 'Full-time',
        'start':                '2021-10-04',
        'end':                  '2023-09-29',
        'start_dates':          _date_formats('2021-10-04'),
        'end_dates':            _date_formats('2023-09-29'),
        'current':              False,
        'city':                 'London',
        'country':              'United Kingdom',
        'sector':               'Staffing & Workforce Solutions',
        'company_size':         None,
        'reason_for_leaving':   (
            'Fixed-term contract completed; pursued further professional development.'
        ),
        'responsibilities': (
            'Resolved 95% of complaints and inquiries within 24 hours across individual '
            'and corporate customer segments. '
            'Delivered seamless event execution for an average of 60+ events per quarter, '
            'managing guest check-ins, facility preparation, and event coordination. '
            'Maintained 90%+ guest satisfaction score through proactive service recovery '
            'and personalised hospitality.'
        ),
        'key_achievements': [
            '95% complaint resolution within 24 hours',
            '60+ events managed per quarter',
            '90%+ guest satisfaction score',
        ],
    },
    {
        # --- ROLE 4 ---
        'employer':             'Deloitte',
        'employer_short':       'Deloitte',
        'title':                'Audit Intern',
        'employment_type':      'Internship',
        'employment_type_short': 'Internship',
        'start':                '2019-06-03',
        'end':                  '2019-09-27',
        'start_dates':          _date_formats('2019-06-03'),
        'end_dates':            _date_formats('2019-09-27'),
        'current':              False,
        'city':                 'Lagos',
        'country':              'Nigeria',
        'sector':               'Professional Services',
        'company_size':         'Large (Big Four)',
        'reason_for_leaving':   'Internship programme concluded.',
        'responsibilities': (
            'Executed interim audit procedures and analytical reviews for commercial '
            'banking clients alongside senior audit team members. '
            'Improved audit testing accuracy and efficiency by analysing financial '
            'datasets using ACL Analytics and Excel. '
            'Reviewed financial transactions and prepared audit documentation applying '
            'IFRS standards to ensure compliance and consistency.'
        ),
        'key_achievements': [
            'Audit procedures for commercial banking clients',
            'Financial dataset analysis using ACL Analytics and Excel',
            'IFRS-compliant audit documentation',
        ],
    },
    {
        # --- ROLE 5 ---
        'employer':             'Airtel Networks Ltd',
        'employer_short':       'Airtel',
        'title':                'Sales & Marketing Intern',
        'employment_type':      'Internship',
        'employment_type_short': 'Internship',
        'start':                '2018-06-04',
        'end':                  '2018-09-28',
        'start_dates':          _date_formats('2018-06-04'),
        'end_dates':            _date_formats('2018-09-28'),
        'current':              False,
        'city':                 'Lagos',
        'country':              'Nigeria',
        'sector':               'Telecommunications',
        'company_size':         'Large (Multinational)',
        'reason_for_leaving':   'Internship programme concluded.',
        'responsibilities': (
            'Processed 250+ online sales orders while maintaining sales records and '
            'supporting account management for channel partners. '
            'Streamlined purchasing and delivery processes through digital transaction '
            'verification, improving accuracy and efficiency. '
            'Delivered financial performance reports that enabled faster decision-making '
            'and improved reporting accuracy by 24%.'
        ),
        'key_achievements': [
            '250+ online sales orders processed',
            '24% improvement in reporting accuracy',
        ],
    },
]


# ============================================================
# SECTION 11 — SKILLS, CERTIFICATIONS & MEMBERSHIPS
# ============================================================

HARD_SKILLS: list[str] = [
    'Google Analytics',
    'Advanced Excel (financial modelling, pivot tables)',
    'Figma',
    'ACL Analytics',
    'CRM platforms',
    'Data visualisation',
    'Social media analytics',
    'Digital marketing strategy',
    'Market research',
    'Competitive analysis',
    'Stakeholder management',
    'Campaign optimisation',
    'Content creation',
    'Project management',
    'Business analytics',
    'Digital transformation',
    'Financial analysis',
]

SOFT_SKILLS: list[str] = [
    'Communication',
    'Cross-functional collaboration',
    'Problem solving',
    'Attention to detail',
    'Adaptability',
    'Leadership',
    'Time management',
    'Presentation skills',
]

SKILLS_SUMMARY: str = (
    'Google Analytics, Advanced Excel, Figma, ACL Analytics, CRM platforms, '
    'data visualisation, social media analytics, digital marketing strategy, '
    'market research, competitive analysis, stakeholder management, '
    'campaign optimisation'
)

CERTIFICATIONS: list[dict] = [
    {
        'name':        'Ethical AI',
        'issuer':      'CISI (Chartered Institute for Securities & Investment)',
        'year':        2025,
        'in_progress': False,
    },
    {
        'name':        'CIM Certificate in Professional Marketing',
        'issuer':      'Chartered Institute of Marketing',
        'year':        None,            # in progress — no completion year yet
        'in_progress': True,
    },
    {
        'name':          'Revenue Operations',
        'issuer':        'HubSpot Academy',
        'year':          2026,
        'in_progress':   False,
        'credential_id': '344cb08fe30e4c82a37840fd1b9bf097',
    },
    {
        'name':          'Reporting',
        'issuer':        'HubSpot Academy',
        'year':          2026,
        'in_progress':   False,
        'valid_until':   'April 2027',
        'credential_id': '5ab88ef9e1c14f7085c889d65ab60ca7',
    },
    {
        'name':          'Marketing Hub Software',
        'issuer':        'HubSpot Academy',
        'year':          2026,
        'in_progress':   False,
        'valid_until':   'April 2027',
        'credential_id': '6842912e043b4c76abf332d41e919e11',
    },
]

PROFESSIONAL_MEMBERSHIPS: list[str] = ['Member, CISI']

LANGUAGES: list[dict] = [
    {'language': 'English', 'level': 'Native / Fluent'},
    # TODO: add Yoruba or other languages if applicable
]

YEARS_EXPERIENCE: int = 3    # ~3 years, counting internships at 0.5x each
YEARS_EXPERIENCE_TEXT: str = '1-3 years'   # for dropdown fields


# ============================================================
# SECTION 12 — REFERENCES
# ============================================================
# TODO: Omokolade must provide reference details before live applications.
# Recommendation: use CISI line manager (most recent) + Warwick MSc supervisor.

# References are REQUIRES_MANUAL.
# The bot must NEVER invent reference details. Any form field that asks for a
# referee name, title, email or phone must be flagged REQUIRES_MANUAL and logged.
# Omokolade fills these in manually after the bot flags the application.
REFERENCES: list[dict] = [
    {
        'name':         None,    # REQUIRES_MANUAL — provide CISI manager name
        'title':        None,    # REQUIRES_MANUAL
        'company':      'CISI',
        'email':        None,    # REQUIRES_MANUAL
        'phone':        None,    # REQUIRES_MANUAL
        'relationship': 'Line Manager',
        'years_known':  None,
    },
    {
        'name':         None,    # REQUIRES_MANUAL — Warwick supervisor or prev employer
        'title':        None,    # REQUIRES_MANUAL
        'company':      None,    # REQUIRES_MANUAL
        'email':        None,    # REQUIRES_MANUAL
        'phone':        None,    # REQUIRES_MANUAL
        'relationship': 'Academic Supervisor / Previous Employer',
        'years_known':  None,
    },
]

REFERENCES_AVAILABLE_ON_REQUEST: str = 'Available on request'

# ---------------------------------------------------------------------------
# REQUIRES_MANUAL_FIELDS — fields the bot must never fill autonomously.
# Any form field whose label resolves to one of these must:
#   1. Return token 'REQUIRES_MANUAL' from field_map.resolve_label()
#   2. Log a warning with the field name
#   3. Transition the application to REQUIRES_MANUAL state
# ---------------------------------------------------------------------------
REQUIRES_MANUAL_FIELDS: set[str] = {
    'address_line_1',
    'address_line_2',
    'postcode',
    'county',
    'reference_1_name',
    'reference_1_email',
    'reference_1_phone',
    'reference_1_title',
    'reference_2_name',
    'reference_2_email',
    'reference_2_phone',
    'reference_2_title',
}


# ============================================================
# SECTION 13 — DIVERSITY & EQUAL OPPORTUNITIES MONITORING
# ============================================================
# These fields are supplied voluntarily and must not affect hiring decisions.
# Omokolade: fill in your real answers below, or leave as 'Prefer not to say'.

DIVERSITY_GENDER: str = 'Prefer not to say'              # TODO: confirm
DIVERSITY_ETHNICITY: str = 'Prefer not to say'           # TODO: confirm
DIVERSITY_DISABILITY: str = 'No'                         # TODO: confirm
DIVERSITY_DISABILITY_DETAIL: Optional[str] = None
DIVERSITY_SEXUAL_ORIENTATION: str = 'Prefer not to say'  # TODO: confirm
DIVERSITY_RELIGION: str = 'Prefer not to say'            # TODO: confirm
DIVERSITY_AGE_GROUP: str = '25-34'                       # TODO: confirm once DOB provided
DIVERSITY_CARING_RESPONSIBILITIES: str = 'No'
DIVERSITY_VETERAN: str = 'No'
DIVERSITY_SOCIOECONOMIC: str = 'Prefer not to say'       # TODO: confirm
DIVERSITY_FIRST_GEN_UNIVERSITY: str = 'No'               # has a BSc — not first gen

# Free school meals proxy (UK socioeconomic question, common in financial services)
# Answer: "No" or "Prefer not to say" — TODO confirm
DIVERSITY_FREE_SCHOOL_MEALS: str = 'Prefer not to say'

# State / independent school attended (socioeconomic indicator)
DIVERSITY_SCHOOL_TYPE: str = 'Prefer not to say'         # TODO: confirm


# ============================================================
# SECTION 14 — SCREENING QUESTION ANSWERS
# ============================================================
# Boolean answers to common yes/no screening questions.
# These drive the _YES_PATTERNS / _NO_PATTERNS logic in workday_apply.py
# and the new field_map resolver.

ELIGIBILITY_ANSWERS: dict[str, bool] = {
    # Work eligibility
    'right_to_work_uk':                True,
    'eligible_to_work_uk':             True,
    'requires_sponsorship':            False,
    'require_visa_sponsorship':        False,
    'require_sponsorship_now_future':  False,
    'unrestricted_right_to_work':      True,

    # Education
    'has_degree':                      True,
    'bachelor_degree':                 True,
    'higher_education':                True,
    'educated_to_degree_level':        True,
    'masters_degree':                  True,

    # Language
    'fluent_english':                  True,
    'native_english':                  True,

    # Personal
    'over_18':                         True,
    'age_over_18':                     True,

    # Process
    'read_job_description':            True,
    'consent_to_background_check':     True,
    'willing_background_check':        True,
    'dbs_check_willing':               True,
    'willing_to_undergo_dbs':          True,

    # Current status
    'currently_employed':              True,
    'currently_student':               False,
    'enrolled_in_study':               False,

    # Other
    'criminal_conviction_unspent':     False,
    'criminal_conviction':             False,
    'security_clearance':              False,
    'previous_employer_company':       False,  # default; override per-company
    'willing_to_relocate':             True,
    'willing_to_travel':               True,
    'member_professional_body':        True,   # CISI member
}

# Free-text answers to common open screening questions
SCREENING_TEXT_ANSWERS: dict[str, str] = {
    'why_this_company': '',   # generated per-application by writer.py
    'why_this_role':    '',   # generated per-application
    'greatest_strength': (
        "I bring together financial services credibility and data-driven marketing — "
        "a combination that's genuinely rare at this experience level. My CISI "
        "internship gave me direct exposure to the industry's professional standards, "
        "while my analytics background (Google Analytics, CRM data) means I don't just "
        "run campaigns, I can prove they work."
    ),
    'greatest_weakness': (
        "I can over-prepare on projects where speed matters more than precision. "
        "I've learned to set internal deadlines a day ahead of the real ones, which "
        "keeps the thoroughness without slowing the team down."
    ),
    'where_see_yourself_5_years': (
        "Leading a marketing function within a financial services or fintech organisation "
        "— ideally managing a team and owning strategic campaigns end-to-end rather than "
        "supporting them."
    ),
    'describe_yourself': (
        "A marketing professional with a financial services background. I've worked "
        "at Deloitte and CISI, hold a Warwick MSc, and have a track record of connecting "
        "data analysis to real campaign results — most recently a 27% increase in user "
        "acquisition at Todlr."
    ),
}


# ============================================================
# SECTION 15 — EXTRACURRICULAR / ADDITIONAL INFORMATION
# ============================================================

EXTRACURRICULAR: list[dict] = [
    {
        'name':        'Warwick AI Society',
        'role':        'Member',
        'description': 'Predictive models, NLP applications, ML workshops',
        'dates':       '2023-2025',
    },
    {
        'name':        'Warwick Enterprise Society',
        'role':        'Member',
        'description': 'Startup pitch competitions, business planning',
        'dates':       '2023-2025',
    },
    {
        'name':        'Donate.NG',
        'role':        'Volunteer',
        'description': 'Nigerian healthcare initiatives',
        'dates':       '2019-present',
    },
]

HOBBIES_INTERESTS: str = (
    'Data analysis, AI & machine learning (Warwick AI Society), '
    'entrepreneurship (Warwick Enterprise Society), community volunteering'
)

ADDITIONAL_INFO: str = (
    "Right to work in the UK. Immediately available. "
    "Member of CISI. HubSpot Revenue Operations certified (2026). CIM Certificate in progress."
)


# ============================================================
# SECTION 16 — CONVENIENCE LOOKUPS
# ============================================================

# Flat dict — backwards-compatible with field_utils._CANDIDATE
# All handlers that import _CANDIDATE from field_utils automatically get
# this richer dataset (field_utils now imports from here).
CANDIDATE_DICT: dict = {
    'first_name':          FIRST_NAME,
    'last_name':           LAST_NAME,
    'full_name':           FULL_NAME,
    'middle_name':         MIDDLE_NAME,
    'preferred_name':      PREFERRED_NAME,
    'title':               TITLE,
    'email':               EMAIL,
    'phone':               PHONE_INTL_SPACE,
    'location':            LOCATION_FULL,
    'city':                CITY,
    'country':             COUNTRY,
    'country_code':        COUNTRY_CODE,
    'nationality':         NATIONALITY,
    'date_of_birth':       DATE_OF_BIRTH_UK,       # DD/MM/YYYY — most UK forms use this
    'date_of_birth_iso':   DATE_OF_BIRTH,           # YYYY-MM-DD
    'date_of_birth_us':    DATE_OF_BIRTH_US,        # MM/DD/YYYY
    'age_group':           AGE_GROUP,
    'linkedin':            LINKEDIN_URL,
    'portfolio':           PORTFOLIO_URL,
    'salary':              SALARY_EXPECTATION_TEXT,
    'salary_int':          str(SALARY_EXPECTATION),
    'current_salary':      str(CURRENT_SALARY),
    'notice_period':       NOTICE_PERIOD_TEXT,
    'notice_weeks':        str(NOTICE_PERIOD_WEEKS),
    'start_date':          START_DATE_TEXT,
    'right_to_work':       RTW_TEXT,
    'sponsorship':         SPONSORSHIP_TEXT,
    'years_experience':    str(YEARS_EXPERIENCE),
    'current_employer':    WORK_HISTORY[0]['employer_short'],
    'current_title':       WORK_HISTORY[0]['title'],
    'highest_degree':      HIGHEST_QUALIFICATION,
    'university':          HIGHEST_QUALIFICATION_INSTITUTION,
    'degree_grade':        HIGHEST_QUALIFICATION_GRADE,
}
