"""ISO-style date formats, English language.

Django's own `en` locale module (`django/conf/locale/en/formats.py`) defines
DATETIME_FORMAT as 'N j, Y, P' -> "Aug. 11, 2026, 3:22 p.m.", and a locale format
module beats the DATETIME_FORMAT *setting*, so setting that in settings.py has no
effect. FORMAT_MODULE_PATH is the supported way to override formats without
changing LANGUAGE_CODE: the language stays English, only the number/date
rendering changes. Django checks this package before its own locale directory.

Reached because settings.FORMAT_MODULE_PATH points here and LANGUAGE_CODE is
'en-us', which resolves to the locale list ['en_US', 'en'].

Anything not defined here falls through to Django's `en` module - deliberately,
for DATE_INPUT_FORMATS and DATETIME_INPUT_FORMATS, whose first entries are
already ISO ('%Y-%m-%d'), so parsing keeps accepting everything it used to.
YEAR_MONTH_FORMAT and MONTH_DAY_FORMAT are also left alone: they render prose
like "August 2026" in admin date drill-downs, where ISO reads worse.
"""

DATE_FORMAT = 'Y-m-d'
DATETIME_FORMAT = 'Y-m-d H:i'
TIME_FORMAT = 'H:i'
SHORT_DATE_FORMAT = 'Y-m-d'
SHORT_DATETIME_FORMAT = 'Y-m-d H:i'
