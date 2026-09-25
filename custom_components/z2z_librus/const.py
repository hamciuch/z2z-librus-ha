DOMAIN = "z2z_librus"
PLATFORMS = ["sensor", "calendar", "select", "text", "button"]

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30

CONF_LUNCH_ENABLED = "lunch_enabled"
CONF_LUNCH_TIME_MONDAY = "lunch_time_monday"
CONF_LUNCH_TIME_TUESDAY = "lunch_time_tuesday"
CONF_LUNCH_TIME_WEDNESDAY = "lunch_time_wednesday"
CONF_LUNCH_TIME_THURSDAY = "lunch_time_thursday"
CONF_LUNCH_TIME_FRIDAY = "lunch_time_friday"

DEFAULT_LUNCH_ENABLED = True
DEFAULT_LUNCH_TIME = "11:45:00"
LUNCH_DURATION_MINUTES = 15

# Replying to / sending messages (creates real messages in Librus) - opt-in.
CONF_REPLIES_ENABLED = "replies_enabled"
DEFAULT_REPLIES_ENABLED = False

# Non-numeric grade symbols used in Librus Synergia (legend on the grades
# page). Schools can define their own, so unknown symbols are still kept
# as-is and reported with label None.
GRADE_SYMBOLS = {
    "+": "plus",
    "-": "minus",
    "np": "nieprzygotowany",
    "bz": "brak zadania",
    "bk": "brak książki/zeszytu",
    "nb": "nieobecny",
    "nk": "nieklasyfikowany",
    "uł": "uczestniczył",
    "nł": "nie uczestniczył",
    "zl": "zaliczył",
    "nz": "nie zaliczył",
    "zw": "zwolniony",
    "uc": "uczęszczał",
    "nu": "nie uczęszczał",
    "0": "zero",
}
