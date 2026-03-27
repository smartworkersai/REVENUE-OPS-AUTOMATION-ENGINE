"""
candidate/field_map.py — Maps every known form label variant to the correct
candidate value.

Usage
-----
    from candidate.field_map import resolve_label, get_field_value

    value = resolve_label("What is your expected salary?")
    # → "32000"

    value = get_field_value("first_name")
    # → "Omokolade"

How it works
------------
1. FIELD_MAP — a dict[str, str] where every key is a normalised label variant
   (lower-cased, stripped, common punctuation removed) and every value is a
   profile key name (matching CANDIDATE_DICT keys or special tokens like
   PHONE, FILE, COVER_LETTER, DROPDOWN:..., SKIP, REQUIRES_MANUAL).

2. resolve_label(label_text) — normalises the input label and looks it up in
   FIELD_MAP.  Falls back to fuzzy matching via fuzzywuzzy if no exact match.
   Returns a (profile_key, value) tuple.

3. get_field_value(key) — returns the string value for a profile key from
   CANDIDATE_DICT, or None for keys that need special handling.

Special return tokens
---------------------
  PHONE          — use fill_phone() with PHONE_FORMATS
  FILE           — use upload_file() with cv_path
  COVER_LETTER   — use cover_letter text
  DROPDOWN:xxx   — use select_react_dropdown() with value xxx
  SKIP           — skip this field (optional, honeypot-risk, or not applicable)
  REQUIRES_MANUAL — flag for human review
  TODO:{key}     — data is missing; log a warning and skip
"""

from __future__ import annotations
from typing import Optional

from candidate.profile import CANDIDATE_DICT, ELIGIBILITY_ANSWERS, REQUIRES_MANUAL_FIELDS, \
    DIVERSITY_GENDER, DIVERSITY_ETHNICITY, DIVERSITY_DISABILITY, DIVERSITY_SEXUAL_ORIENTATION, \
    DIVERSITY_RELIGION, DIVERSITY_AGE_GROUP, DIVERSITY_VETERAN, DIVERSITY_CARING_RESPONSIBILITIES, \
    DIVERSITY_SOCIOECONOMIC, DIVERSITY_FREE_SCHOOL_MEALS, DIVERSITY_SCHOOL_TYPE, \
    ADDRESS_LINE_1, ADDRESS_LINE_2, POSTCODE, COUNTY, DATE_OF_BIRTH, CURRENT_SALARY, \
    REFERENCES, SALARY_EXPECTATION, SALARY_MINIMUM, SALARY_MAXIMUM, \
    YEARS_EXPERIENCE_TEXT, SKILLS_SUMMARY, ADDITIONAL_INFO, \
    NATIONALITY, TITLE, PHONE_INTL_SPACE, PREFERRED_NAME, \
    START_DATE_TEXT, NOTICE_PERIOD_TEXT, EMPLOYMENT_STATUS, \
    HIGHEST_QUALIFICATION, HIGHEST_QUALIFICATION_SUBJECT, \
    UNDERGRADUATE_DEGREE, UNDERGRADUATE_INSTITUTION, UNDERGRADUATE_GRADE, \
    PROFESSIONAL_MEMBERSHIPS, LINKEDIN_URL, PHONE_FORMATS, PORTFOLIO_URL

# ---------------------------------------------------------------------------
# Core field map — label text → profile key or special token
#
# Keys must be LOWER-CASED and stripped of leading/trailing whitespace.
# Apostrophes, question marks, colons, asterisks removed for matching.
# ---------------------------------------------------------------------------

# fmt: off
FIELD_MAP: dict[str, str] = {

    # ---- PERSONAL DETAILS ------------------------------------------------
    'title':                               'title',
    'salutation':                          'title',
    'honorific':                           'title',
    'prefix':                              'title',

    'first name':                          'first_name',
    'firstname':                           'first_name',
    'first_name':                          'first_name',
    'given name':                          'first_name',
    'given_name':                          'first_name',
    'forename':                            'first_name',
    'christian name':                      'first_name',
    'your first name':                     'first_name',

    'last name':                           'last_name',
    'lastname':                            'last_name',
    'last_name':                           'last_name',
    'surname':                             'last_name',
    'family name':                         'last_name',
    'family_name':                         'last_name',
    'your last name':                      'last_name',
    'your surname':                        'last_name',

    'middle name':                         'middle_name',
    'middle_name':                         'middle_name',
    'middle initial':                      'middle_name',

    'full name':                           'full_name',
    'full_name':                           'full_name',
    'name':                                'full_name',
    'your name':                           'full_name',
    'candidate name':                      'full_name',
    'applicant name':                      'full_name',

    'preferred name':                      'preferred_name',
    'preferred_name':                      'preferred_name',
    'known as':                            'preferred_name',
    'nickname':                            'preferred_name',

    'date of birth':                       'date_of_birth',
    'dob':                                 'date_of_birth',
    'birth date':                          'date_of_birth',
    'birthday':                            'date_of_birth',
    'date of birth (dd/mm/yyyy)':          'date_of_birth',
    'date of birth (mm/dd/yyyy)':          'date_of_birth_us',

    'national insurance number':           'TODO:national_insurance_number',
    'ni number':                           'TODO:national_insurance_number',
    'national insurance':                  'TODO:national_insurance_number',
    'nino':                                'TODO:national_insurance_number',

    # ---- CONTACT ----------------------------------------------------------
    'email':                               'email',
    'email address':                       'email',
    'email_address':                       'email',
    'e-mail':                              'email',
    'e-mail address':                      'email',
    'your email':                          'email',
    'contact email':                       'email',
    'work email':                          'email',
    'personal email':                      'email',

    'confirm email':                       'email',
    'confirm email address':               'email',
    'email confirmation':                  'email',
    're-enter email':                      'email',
    'repeat email':                        'email',

    'phone':                               'PHONE',
    'phone number':                        'PHONE',
    'phone_number':                        'PHONE',
    'mobile':                              'PHONE',
    'mobile number':                       'PHONE',
    'mobile phone':                        'PHONE',
    'mobile_number':                       'PHONE',
    'telephone':                           'PHONE',
    'telephone number':                    'PHONE',
    'cell':                                'PHONE',
    'cell phone':                          'PHONE',
    'contact number':                      'PHONE',
    'contact phone':                       'PHONE',
    'primary phone':                       'PHONE',
    'home phone':                          'PHONE',
    'work phone':                          'PHONE',
    'daytime phone':                       'PHONE',

    # ---- ADDRESS ----------------------------------------------------------
    'address':                             'REQUIRES_MANUAL',
    'address line 1':                      'REQUIRES_MANUAL',
    'address line 2':                      'REQUIRES_MANUAL',
    'street address':                      'REQUIRES_MANUAL',
    'street':                              'REQUIRES_MANUAL',
    'building name':                       'REQUIRES_MANUAL',
    'flat':                                'REQUIRES_MANUAL',
    'apartment':                           'REQUIRES_MANUAL',

    'city':                                'city',
    'town':                                'city',
    'city/town':                           'city',
    'town/city':                           'city',
    'city or town':                        'city',
    'municipality':                        'city',

    'county':                              'REQUIRES_MANUAL',
    'region':                              'REQUIRES_MANUAL',
    'state':                               'REQUIRES_MANUAL',
    'province':                            'REQUIRES_MANUAL',
    'county/region':                       'REQUIRES_MANUAL',

    'postcode':                            'REQUIRES_MANUAL',
    'post code':                           'REQUIRES_MANUAL',
    'zip code':                            'REQUIRES_MANUAL',
    'zip':                                 'REQUIRES_MANUAL',
    'postal code':                         'REQUIRES_MANUAL',

    'country':                             'country',
    'country of residence':                'country',
    'country of domicile':                 'country',
    'home country':                        'country',
    'current country':                     'country',

    'location':                            'location',
    'current location':                    'location',
    'where are you based':                 'location',
    'where do you live':                   'location',

    # ---- ONLINE PRESENCE --------------------------------------------------
    'linkedin':                            'linkedin',
    'linkedin url':                        'linkedin',
    'linkedin profile':                    'linkedin',
    'linkedin profile url':                'linkedin',
    'linkedin_profile':                    'linkedin',
    'linkedin profile link':               'linkedin',
    'your linkedin':                       'linkedin',

    'github':                              'SKIP',
    'github url':                          'SKIP',
    'github profile':                      'SKIP',
    'portfolio':                           'portfolio',
    'portfolio url':                       'portfolio',
    'portfolio link':                      'portfolio',
    'portfolio website':                   'portfolio',
    'work samples':                        'portfolio',
    'website':                             'portfolio',
    'personal website':                    'portfolio',
    'personal website url':                'portfolio',
    'twitter':                             'SKIP',
    'twitter url':                         'SKIP',

    # ---- NATIONALITY / ELIGIBILITY ----------------------------------------
    'nationality':                         'nationality',
    'citizenship':                         'nationality',
    'country of citizenship':              'nationality',
    'country of nationality':              'nationality',

    'right to work in the uk':             'right_to_work',
    'right to work':                       'right_to_work',
    'eligible to work in the uk':          'right_to_work',
    'do you have the right to work':       'right_to_work',
    'are you eligible to work':            'right_to_work',
    'work authorisation':                  'right_to_work',
    'work authorization':                  'right_to_work',
    'authorised to work in the uk':        'right_to_work',
    'unrestricted right to work':          'right_to_work',

    'visa sponsorship required':           'sponsorship',
    'require sponsorship':                 'sponsorship',
    'do you require visa sponsorship':     'sponsorship',
    'sponsorship required':                'sponsorship',
    'will you require sponsorship':        'sponsorship',
    'will you now or in the future require sponsorship': 'sponsorship',

    'security clearance':                  'DROPDOWN:None',
    'level of clearance':                  'DROPDOWN:None',
    'do you hold security clearance':      'SKIP',

    'willing to undergo dbs check':        'SKIP',   # handled by ELIGIBILITY_ANSWERS
    'dbs check':                           'SKIP',

    # ---- EMPLOYMENT & AVAILABILITY ----------------------------------------
    'notice period':                       'notice_period',
    'notice_period':                       'notice_period',
    'when can you start':                  'start_date',
    'available start date':                'start_date',
    'earliest start date':                 'start_date',
    'start date':                          'start_date',
    'availability':                        'start_date',
    'how soon can you start':              'start_date',

    'currently employed':                  'SKIP',   # handled by ELIGIBILITY_ANSWERS
    'are you currently employed':          'SKIP',
    'employment status':                   'SKIP',

    'preferred employment type':           'SKIP',
    'employment type':                     'SKIP',
    'full-time or part-time':              'SKIP',

    # ---- SALARY -----------------------------------------------------------
    'salary expectation':                  'salary',
    'expected salary':                     'salary',
    'desired salary':                      'salary',
    'salary expectations':                 'salary',
    'what is your expected salary':        'salary',
    'what salary are you expecting':       'salary',
    'minimum salary':                      'salary',
    'salary requirement':                  'salary',
    'compensation expectation':            'salary',
    'desired compensation':                'salary',
    'target salary':                       'salary',
    'requested salary':                    'salary',
    'salary (gbp)':                        'salary',
    'annual salary expectation':           'salary',

    'current salary':                      'current_salary',
    'most recent salary':                  'current_salary',
    'previous salary':                     'current_salary',
    'last salary':                         'current_salary',
    'current base salary':                 'current_salary',

    # ---- EDUCATION --------------------------------------------------------
    'highest qualification':               'highest_degree',
    'highest level of education':          'highest_degree',
    'education level':                     'highest_degree',
    'degree level':                        'highest_degree',
    'level of education':                  'highest_degree',
    'qualifications':                      'highest_degree',
    'academic qualification':              'highest_degree',

    'university':                          'university',
    'university/college':                  'university',
    'college':                             'university',
    'institution':                         'university',
    'school':                              'university',
    'school/university':                   'university',
    'educational institution':             'university',
    'name of institution':                 'university',
    'name of university':                  'university',

    'degree':                              'highest_degree',
    'degree subject':                      'DROPDOWN:Innovation & Entrepreneurship',
    'degree title':                        'highest_degree',
    'course title':                        'highest_degree',
    'subject studied':                     'DROPDOWN:Innovation & Entrepreneurship',
    'field of study':                      'DROPDOWN:Innovation & Entrepreneurship',
    'major':                               'DROPDOWN:Accounting / Innovation',
    'area of study':                       'DROPDOWN:Innovation & Entrepreneurship',

    'degree classification':               'degree_grade',
    'degree grade':                        'degree_grade',
    'grade':                               'degree_grade',
    'result':                              'degree_grade',
    'classification':                      'degree_grade',
    'degree result':                       'degree_grade',

    'gpa':                                 'DROPDOWN:3.5',
    'grade point average':                 'DROPDOWN:3.5',

    'graduation year':                     'DROPDOWN:2025',
    'year of graduation':                  'DROPDOWN:2025',
    'year graduated':                      'DROPDOWN:2025',

    # ---- WORK HISTORY (generic — resolved to WORK_HISTORY[0]) ------------
    'employer':                            'current_employer',
    'company':                             'current_employer',
    'organisation':                        'current_employer',
    'organization':                        'current_employer',
    'employer name':                       'current_employer',
    'company name':                        'current_employer',
    'current employer':                    'current_employer',
    'most recent employer':                'current_employer',
    'most recent company':                 'current_employer',

    'job title':                           'current_title',
    'position':                            'current_title',
    'role':                                'current_title',
    'current job title':                   'current_title',
    'most recent job title':               'current_title',
    'most recent position':                'current_title',

    'years of experience':                 'years_experience',
    'years experience':                    'years_experience',
    'how many years experience':           'years_experience',
    'total years experience':              'years_experience',
    'how long have you worked':            'years_experience',
    'relevant experience':                 'years_experience',

    # ---- CV / DOCUMENTS --------------------------------------------------
    'resume':                              'FILE',
    'cv':                                  'FILE',
    'upload cv':                           'FILE',
    'upload resume':                       'FILE',
    'attach cv':                           'FILE',
    'attach resume':                       'FILE',
    'resume/cv':                           'FILE',
    'curriculum vitae':                    'FILE',

    'cover letter':                        'COVER_LETTER',
    'covering letter':                     'COVER_LETTER',
    'cover letter (optional)':             'COVER_LETTER',
    'personal statement':                  'COVER_LETTER',
    'supporting statement':                'COVER_LETTER',
    'motivation letter':                   'COVER_LETTER',
    'letter of motivation':                'COVER_LETTER',
    'application letter':                  'COVER_LETTER',
    'why do you want to work here':        'COVER_LETTER',
    'why are you interested in this role': 'COVER_LETTER',

    # ---- SKILLS ----------------------------------------------------------
    'key skills':                          'DROPDOWN:Google Analytics, Excel, Figma, CRM',
    'skills':                              'DROPDOWN:Google Analytics, Excel, Figma, CRM',
    'technical skills':                    'DROPDOWN:Google Analytics, Excel, Figma',
    'additional skills':                   'SKIP',
    'other skills':                        'SKIP',

    # ---- PROFESSIONAL MEMBERSHIPS / CERTIFICATIONS -----------------------
    'professional memberships':            'DROPDOWN:Member, CISI',
    'memberships':                         'DROPDOWN:Member, CISI',
    'professional body':                   'DROPDOWN:CISI',
    'certifications':                      'DROPDOWN:Ethical AI (CISI, 2025)',
    'qualifications and certifications':   'DROPDOWN:Ethical AI (CISI, 2025)',

    # ---- REFERENCES -------------------------------------------------------
    'reference name':                      'REQUIRES_MANUAL',
    'reference 1 name':                    'REQUIRES_MANUAL',
    'first reference':                     'REQUIRES_MANUAL',
    'referee name':                        'REQUIRES_MANUAL',
    'reference job title':                 'REQUIRES_MANUAL',
    'reference company':                   'REQUIRES_MANUAL',
    'reference email':                     'REQUIRES_MANUAL',
    'reference phone':                     'REQUIRES_MANUAL',
    'reference 2 name':                    'REQUIRES_MANUAL',
    'second reference':                    'REQUIRES_MANUAL',
    'references available':                'DROPDOWN:Available on request',

    # ---- EQUAL OPPORTUNITIES / DIVERSITY MONITORING ----------------------
    'gender':                              'DIVERSITY:gender',
    'sex':                                 'DIVERSITY:gender',
    'gender identity':                     'DIVERSITY:gender',

    'ethnic group':                        'DIVERSITY:ethnicity',
    'ethnicity':                           'DIVERSITY:ethnicity',
    'ethnic background':                   'DIVERSITY:ethnicity',
    'ethnic origin':                       'DIVERSITY:ethnicity',
    'what is your ethnic group':           'DIVERSITY:ethnicity',

    'disability':                          'DIVERSITY:disability',
    'do you consider yourself disabled':   'DIVERSITY:disability',
    'do you have a disability':            'DIVERSITY:disability',
    'disability or health condition':      'DIVERSITY:disability',

    'sexual orientation':                  'DIVERSITY:sexual_orientation',
    'sexuality':                           'DIVERSITY:sexual_orientation',

    'religion':                            'DIVERSITY:religion',
    'religion or belief':                  'DIVERSITY:religion',
    'faith':                               'DIVERSITY:religion',

    'age':                                 'DIVERSITY:age_group',
    'age group':                           'DIVERSITY:age_group',
    'age bracket':                         'DIVERSITY:age_group',

    'veteran':                             'DIVERSITY:veteran',
    'military veteran':                    'DIVERSITY:veteran',
    'armed forces':                        'DIVERSITY:veteran',
    'serving or have served':              'DIVERSITY:veteran',

    'caring responsibilities':             'DIVERSITY:caring',
    'carer':                               'DIVERSITY:caring',

    'socioeconomic background':            'DIVERSITY:socioeconomic',
    'free school meals':                   'DIVERSITY:free_school_meals',
    'type of school attended':             'DIVERSITY:school_type',
    'school type':                         'DIVERSITY:school_type',

    # ---- ADDITIONAL / MISC -----------------------------------------------
    'additional information':             'additional_info',
    'anything else':                      'additional_info',
    'other information':                  'additional_info',
    'any other information':              'additional_info',
    'is there anything else':             'additional_info',

    'how did you hear about us':          'SKIP',
    'where did you hear about this role': 'SKIP',
    'referral source':                    'SKIP',
    'source':                             'SKIP',

    'number of dependants':               'SKIP',
    'dependants':                         'SKIP',
}
# fmt: on


# ---------------------------------------------------------------------------
# Diversity answers lookup
# ---------------------------------------------------------------------------

_DIVERSITY_MAP: dict[str, str] = {
    'gender':              DIVERSITY_GENDER,
    'ethnicity':           DIVERSITY_ETHNICITY,
    'disability':          DIVERSITY_DISABILITY,
    'sexual_orientation':  DIVERSITY_SEXUAL_ORIENTATION,
    'religion':            DIVERSITY_RELIGION,
    'age_group':           DIVERSITY_AGE_GROUP,
    'veteran':             DIVERSITY_VETERAN,
    'caring':              DIVERSITY_CARING_RESPONSIBILITIES,
    'socioeconomic':       DIVERSITY_SOCIOECONOMIC,
    'free_school_meals':   DIVERSITY_FREE_SCHOOL_MEALS,
    'school_type':         DIVERSITY_SCHOOL_TYPE,
}


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

import re as _re

_STRIP_RE = _re.compile(r'[*?:()\[\]\'\"]+')


def _normalise(text: str) -> str:
    """Lower-case, strip, remove common punctuation for fuzzy comparison."""
    return _STRIP_RE.sub('', text).strip().lower()


# Pre-normalised lookup table built once at import time.
# FIELD_MAP keys may contain parentheses/punctuation that _normalise strips from
# incoming labels — matching against raw keys would silently fail.
_FIELD_MAP_NORMALISED: dict[str, str] = {
    _normalise(k): v for k, v in FIELD_MAP.items()
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_field_value(profile_key: str) -> Optional[str]:
    """
    Return the string value for a profile key.
    Returns None for keys that require special handling (PHONE, FILE, etc.)
    or that are missing (TODO:...).
    """
    return CANDIDATE_DICT.get(profile_key)


def resolve_label(label_text: str) -> tuple[str, Optional[str]]:
    """
    Resolve a form label to a (token, value) tuple.

    Token values:
        'PHONE'           → use fill_phone(PHONE_FORMATS)
        'FILE'            → use upload_file(cv_path)
        'COVER_LETTER'    → use cover_letter text
        'SKIP'            → skip this field
        'REQUIRES_MANUAL' → flag for human review
        'DROPDOWN:xxx'    → use select_react_dropdown(value=xxx)
        'DIVERSITY:xxx'   → use diversity monitoring value
        'TODO:xxx'        → data missing; log warning and skip
        'fill'            → fill with returned value

    Returns (token, value) where value is None when token is not 'fill'.
    """
    normalised = _normalise(label_text)

    # Exact match against pre-normalised keys
    token = _FIELD_MAP_NORMALISED.get(normalised)

    # Prefix-based match if no exact match
    if token is None:
        for key, t in _FIELD_MAP_NORMALISED.items():
            if normalised.startswith(key) or key.startswith(normalised):
                token = t
                break

    # Fuzzy fallback using fuzzywuzzy if available
    if token is None:
        try:
            from fuzzywuzzy import process as _fuzz_process
            match, score = _fuzz_process.extractOne(normalised, list(_FIELD_MAP_NORMALISED.keys()))
            if score >= 85:
                token = _FIELD_MAP_NORMALISED[match]
        except Exception:
            pass

    if token is None:
        return 'SKIP', None

    # Handle special tokens
    if token.startswith('TODO:'):
        import logging
        logging.getLogger(__name__).warning(
            'field_map: missing profile data for label %r (key: %s) — skipping',
            label_text, token,
        )
        return 'SKIP', None

    if token == 'PHONE':
        return 'PHONE', None

    if token == 'FILE':
        return 'FILE', None

    if token == 'COVER_LETTER':
        return 'COVER_LETTER', None

    if token == 'SKIP':
        return 'SKIP', None

    if token == 'REQUIRES_MANUAL':
        return 'REQUIRES_MANUAL', None

    if token.startswith('DROPDOWN:'):
        value = token.split(':', 1)[1]
        return 'DROPDOWN', value

    if token.startswith('DIVERSITY:'):
        subkey = token.split(':', 1)[1]
        value = _DIVERSITY_MAP.get(subkey, 'Prefer not to say')
        return 'fill', value

    # Profile key → look up in CANDIDATE_DICT
    value = CANDIDATE_DICT.get(token)
    if value is not None:
        return 'fill', str(value)

    # Direct eligibility boolean answer
    if token in ELIGIBILITY_ANSWERS:
        return 'fill', 'Yes' if ELIGIBILITY_ANSWERS[token] else 'No'

    return 'SKIP', None


def get_eligibility_answer(question_text: str) -> Optional[bool]:
    """
    Given a screening question string, return True/False or None.
    Used by workday_apply and similar handlers to replace inline regex patterns.
    Returns None if the question cannot be determined from profile data.
    """
    text = question_text.lower()

    # Map question patterns to ELIGIBILITY_ANSWERS keys
    _QUESTION_TO_KEY = [
        (['right to work', 'eligible to work', 'authoris', 'authoriz',
          'unrestricted right'],              'right_to_work_uk'),
        (['visa sponsor', 'require sponsor',
          'sponsorship required'],            'requires_sponsorship'),
        (['degree', 'bachelor', 'higher education',
          'educated to degree'],              'has_degree'),
        (['fluent', 'english'],               'fluent_english'),
        (['over 18', '18 or over', 'age 18',
          '18 years'],                        'over_18'),
        (['background check', 'dbs', 'consent to background'],
                                              'consent_to_background_check'),
        (['read.*job description'],           'read_job_description'),
        (['criminal conviction'],             'criminal_conviction_unspent'),
        (['currently.*student', 'enrolled'],  'currently_student'),
        (['currently employed'],              'currently_employed'),
        (['security clearance'],              'security_clearance'),
        (['unrestricted.*travel'],            'unrestricted_travel'),
        (['member.*professional body', 'cisi'],
                                              'member_professional_body'),
        (['masters', "master's"],             'masters_degree'),
    ]

    import re
    for patterns, key in _QUESTION_TO_KEY:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                answer = ELIGIBILITY_ANSWERS.get(key)
                if answer is not None:
                    return answer

    return None  # unknown — caller should use Claude fallback


# ---------------------------------------------------------------------------
# Selector-to-value mapping for common ATS field selectors
# (CSS selector fragment → profile key, used to seed heuristic fill loops)
# ---------------------------------------------------------------------------

# fmt: off
SELECTOR_MAP: dict[str, tuple[str, str]] = {
    # selector_fragment: (action, profile_key_or_value)

    # Name
    '[data-automation-id="legalNameSection_firstName"]': ('fill', 'first_name'),
    '[data-automation-id="legalNameSection_lastName"]':  ('fill', 'last_name'),
    '#first_name':                                        ('fill', 'first_name'),
    '#last_name':                                         ('fill', 'last_name'),
    '#firstName':                                         ('fill', 'first_name'),
    '#lastName':                                          ('fill', 'last_name'),
    'input[name="name"]':                                 ('fill', 'full_name'),
    'input[name="full_name"]':                            ('fill', 'full_name'),
    'input[name*="first" i]':                             ('fill', 'first_name'),
    'input[name*="last" i]':                              ('fill', 'last_name'),
    'input[name*="firstName" i]':                         ('fill', 'first_name'),
    'input[name*="lastName" i]':                          ('fill', 'last_name'),
    'input[autocomplete*="given-name" i]':                ('fill', 'first_name'),
    'input[autocomplete*="family-name" i]':               ('fill', 'last_name'),
    'input[autocomplete*="name" i]':                      ('fill', 'full_name'),

    # Email
    '#email':                                             ('fill', 'email'),
    'input[name="email"]':                                ('fill', 'email'),
    'input[name*="email" i]':                             ('fill', 'email'),
    'input[type="email"]':                                ('fill', 'email'),
    'input[autocomplete="email"]':                        ('fill', 'email'),
    '[data-automation-id="email"]':                       ('fill', 'email'),

    # Phone
    '#phone':                                             ('phone', ''),
    'input[name="phone"]':                                ('phone', ''),
    'input[name*="phone" i]':                             ('phone', ''),
    'input[type="tel"]':                                  ('phone', ''),
    '[data-automation-id="phone-number"]':                ('phone', ''),
    'input[data-automation-id*="phone" i]':               ('phone', ''),

    # Location
    '#location':                                          ('fill', 'location'),
    'input[name*="location" i]':                          ('fill', 'location'),
    'input[id*="location" i]':                            ('fill', 'location'),
    'input[name*="city" i]':                              ('fill', 'city'),
    'input[name*="postcode" i]':                          ('fill', 'TODO:postcode'),
    'input[name*="zip" i]':                               ('fill', 'TODO:postcode'),
    'input[name*="address" i]':                           ('fill', 'TODO:address_line_1'),

    # LinkedIn
    '#linkedin_profile':                                  ('fill', 'linkedin'),
    'input[name*="linkedin" i]':                          ('fill', 'linkedin'),
    'input[id*="linkedin" i]':                            ('fill', 'linkedin'),

    # File uploads
    'input[type="file"]':                                 ('file', ''),
    '#resume':                                            ('file', ''),
    'input[name*="resume" i]':                            ('file', ''),
    'input[name*="cv" i]':                                ('file', ''),
    '[data-automation-id="file-upload-input-ref"]':       ('file', ''),

    # Cover letter
    '#cover_letter':                                      ('cover_letter', ''),
    'textarea[name*="cover" i]':                          ('cover_letter', ''),
    'textarea[id*="cover" i]':                            ('cover_letter', ''),
    'textarea[name*="supporting" i]':                     ('cover_letter', ''),
    'textarea[name*="motivation" i]':                     ('cover_letter', ''),

    # Salary
    'input[name*="salary" i]':                            ('fill', 'salary'),
    'input[id*="salary" i]':                              ('fill', 'salary'),
    'input[placeholder*="salary" i]':                     ('fill', 'salary'),
    'input[aria-label*="salary" i]':                      ('fill', 'salary'),

    # Country
    '[data-automation-id="country"]':                     ('dropdown', 'United Kingdom'),
    'select[name*="country" i]':                          ('dropdown', 'United Kingdom'),

    # Right to work / eligibility (handled by screening logic, not this map)
    'input[name*="sponsorship" i]':                       ('fill', 'sponsorship'),
    'input[name*="right_to_work" i]':                     ('fill', 'right_to_work'),
}
# fmt: on


def resolve_selector(selector_fragment: str) -> tuple[str, str]:
    """
    Look up a CSS selector fragment and return (action, value).
    Falls back to ('skip', '') if not found.
    """
    result = SELECTOR_MAP.get(selector_fragment)
    if result:
        action, key = result
        if action in ('fill',) and key in CANDIDATE_DICT:
            return 'fill', CANDIDATE_DICT[key]
        if action in ('fill',) and key.startswith('TODO:'):
            return 'skip', ''
        return action, key
    return 'skip', ''
