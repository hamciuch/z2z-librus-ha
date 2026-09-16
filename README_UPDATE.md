# Z2Z Librus v0.2.0

## Added
- grades including descriptive marks such as `+`, `-`, `np`, `bz`
- one device/config entry per Librus account (multi-student)
- student info + lucky number
- attendance records + overall/semester percentage
- homework sensor and native Home Assistant calendar
- received message headers + unread count
- current/next-week timetable + native Home Assistant calendar
- current/next-month Librus schedule/agenda + native Home Assistant calendar
- next lesson sensor
- next agenda event sensor
- failures of optional Librus modules do not take down the whole integration

## Install
Copy `custom_components/z2z_librus` over the existing integration, commit/push,
then update/redownload from HACS and restart Home Assistant.

If HACS does not see 0.2.0 immediately, use Redownload or remove/re-add the custom repository.
