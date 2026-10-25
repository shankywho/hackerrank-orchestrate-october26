# Image evidence transcription (cache for the 11 PNG files in dataset/evidence/images)

Transcribed by Claude Code vision during development (model claude-sonnet-5-5, 2026-10-25). Each image was
viewed directly and the visible fields were typed in by hand. This file is a cache: the normalizer reads it
instead of running a vision model at evaluation time. It is disclosed in the README.

Rules for reading it:
- Only rows that name a resource (or an object/reference label) and show a record state are transcribed.
- An image proves existence and state, never ownership. Emails and user ids seen on screen are kept as
  `visible_*` notes for cross-checks only; ownership comes from LINK_RESOURCE_IDENTITY activity records.
- `kind` is `id` when the resource id is printed on screen, `alias` when only a label is shown (file name or
  customer key); an alias is turned into a resource id only through a link record whose label matches it.
- Times are as printed (the console's own time zone is noted); the observation time is the file's capture time.

Row format (pipe separated, one record per line starting with `OBS |`):
OBS | system | resource_or_label | kind | category | state | visible_email | visible_user_id | visible_phone | visible_times | note

## evidence_04 — account_admin_console_20260926.png
OBS | account_service | account_5762 | id | USER_PROFILE | FOUND | haruka.s1838@zoho.com | user_11 | +81 90 7610 2507 | last contact 24 Sep 2026 09:14 JST; member since 12 Jan 2024 | customer page "Haruka Sato", Silver; support_9538 (CS-8538) only referenced, no state shown

## evidence_09 — search_index_inspector_20260901.png
OBS | search_service | search_428 | id | SEARCH_INDEX_ENTRY | FOUND | g.silva76@gmail.com | user_13 | | indexed 2026-09-01T05:58:21Z; query executed 01 Sep 2026 06:00:14 UTC | display name "Gabriel Silva", index customer_directory_v3
OBS | search_service | search_1874 | id | SEARCH_INDEX_ENTRY | FOUND | g.silva76.jr@gmail.com | | | last indexed 05:55:03 UTC | different email (look-alike), no display name or customer reference

## evidence_14 — connector_management_console_20260807.png
OBS | integration_service | data_flow_1121 | id | DATA_FLOW | ACTIVE | jain.aditi@gmail.com | | | last contact 07:59:42; page time 07 Aug 2026 08:00 UTC | Healthy, Continuous sync, destination customer_analytics_warehouse
OBS | integration_service | data_flow_9846 | id | DATA_FLOW | ACTIVE | lucia-torres@gmail.com | | | last contact 07:59:36 | Healthy, Continuous, destination customer_analytics_warehouse; other rows (order notifications, delivery tracking) have no resource id

## evidence_19 — analytics_profile_view_20260713.png
OBS | analytics_platform | analytics_7438 | id | ANALYTICS_PROFILE | IDENTIFIABLE | darwish.omar@icloud.com | user_20 | | last activity 13 Jul 2026 14:22 UTC; first seen 21 Apr 2026 09:14 UTC | profile header shows name "Omar Darwish", email and user id in clear

## evidence_23 — customer_export_console_20260618.png
OBS | export_service | export_1260 | id | USER_EXPORT | FOUND | ingrid.dahl@outlook.com | | | created 18 Jun 2026 06:42 UTC; expires 25 Jun 2026 06:42 UTC | status Ready, customer_export_1260.zip, 18.4 MB, download count 0
OBS | export_service | export_1261 | id | USER_EXPORT | FOUND | marcus.lee@northstar.shop | | | created 20 Jun 14:18 UTC (after the capture time) | status Collecting, staff-owned
OBS | export_service | export_1259 | id | USER_EXPORT | FOUND | sara.kim@northstar.shop | | | created 17 Jun 11:03 UTC | status Packaging, staff-owned
OBS | export_service | export_1258 | id | USER_EXPORT | FOUND | daniel.park@northstar.shop | | | created 16 Jun 09:27 UTC | status Ready, staff-owned

## evidence_28 — support_ticket_console_20260524.png
OBS | support_platform | support_1034 | id | SUPPORT_MESSAGE | IDENTIFIABLE | kagyeman8947@fastmail.com | | | messages 09:12-09:35; order activity 22-26 May | ticket CS-5034, customer name "Kofi Agyeman", message text shown in clear

## evidence_32 — backup_restore_console_20260429.png
OBS | backup_service | backup_9291 | id | BACKUP_ARCHIVE | ACTIVE | bluepeakco@outlook.com | | | created 26 Apr 02:00 | INFERRED state: archive in Archive tier, no restore-block indicator visible
OBS | backup_service | backup_9288 | id | BACKUP_ARCHIVE | ACTIVE | miller.logistics@company.com | | | created 26 Apr 02:00 | INFERRED state: no restore-block indicator visible
OBS | backup_service | backup_9287 | id | BACKUP_ARCHIVE | ACTIVE | areed1433@gmail.com | | | created 26 Apr 2026 02:00 UTC; retention end 26 Jul 2026; last drill 29 Mar 2026 | INFERRED state: no restore-block indicator visible; path s3://northstar-backups/customer-platform/weekly/segment-42-20260426.tar.zst, region eu-west-1
OBS | backup_service | backup_9286 | id | BACKUP_ARCHIVE | ACTIVE | summit-retail@company.com | | | created 26 Apr 02:00; restored 25 Apr (activity) | INFERRED state: no restore-block indicator visible

## evidence_37 — security_audit_explorer_20260905.png
OBS | security_service | audit_record_003 | id | SECURITY_AUDIT_LOG | FOUND | | user_03 | | recorded 2026-08-25T04:03:00Z | event account_closure_recorded, actor automated account workflow, signature Valid, journal 2026-Q3, fingerprint 8f0c...31ad
OBS | security_service | audit_record_002 | id | SECURITY_AUDIT_LOG | FOUND | | user_03 | | 25 Aug 03:21 UTC | session revoked, Security system
OBS | security_service | audit_record_001 | id | SECURITY_AUDIT_LOG | FOUND | | user_03 | | 24 Aug 16:14 UTC | recovery challenge completed, User (self)
OBS | security_service | audit_record_006 | id | SECURITY_AUDIT_LOG | FOUND | | user_03 | | 22 Aug 11:27 UTC | email change confirmed, User (self)
OBS | security_service | audit_record_005 | id | SECURITY_AUDIT_LOG | FOUND | | user_03 | | 20 Aug 09:42 UTC | login from new device, User (self)
OBS | security_service | audit_record_004 | id | SECURITY_AUDIT_LOG | FOUND | | user_03 | | 18 Aug 14:05 UTC | password changed, User (self)

## evidence_42 — deleted_account_reference_20260310.png
OBS | identity_service | user_17 | alias | ACCOUNT_TOMBSTONE | REDACTED | | user_17 | | created 11 Jan 2024 08:42 UTC; most recent update 10 Mar 2026 07:18 UTC | "Retained reference" customer key; no name, email, phone or hash shown; linked records ORD-20263245, CS-3245, CS-3378, FIN-7742 Active

## evidence_46 — object_storage_browser_20260213.png
OBS | object_storage | delivery_confirmation_19.pdf | alias | SUPPORT_ATTACHMENT | FOUND | | | | modified 12 Feb 2026 18:42 UTC; page captured 13 Feb 2026 08:00 UTC | 684 KB v3, uploader support-service, bucket northstar-customer-data/support-attachments/2026/02
OBS | object_storage | delivery_confirmation_20.pdf | alias | SUPPORT_ATTACHMENT | FOUND | | | | modified 11 Feb 14:17 | 712 KB v1
OBS | object_storage | return_label_4821.pdf | alias | SUPPORT_ATTACHMENT | FOUND | | | | modified 10 Feb 09:33 | 203 KB v1
OBS | object_storage | return_label_4876.pdf | alias | SUPPORT_ATTACHMENT | FOUND | | | | modified 11 Feb 16:05 | 198 KB v2
OBS | object_storage | invoice_NS-10493.pdf | alias | SUPPORT_ATTACHMENT | FOUND | | | | modified 10 Feb 11:21 | 318 KB v1
OBS | object_storage | parcel_damage_7713.jpg | alias | SUPPORT_ATTACHMENT | FOUND | | | | modified 12 Feb 10:08 | 2.4 MB v1
OBS | object_storage | customer_export_2026-02-10.zip | alias | USER_EXPORT | FOUND | | | | modified 10 Feb 23:14 | 128 MB v1
OBS | object_storage | customer_export_2026-02-12.zip | alias | USER_EXPORT | FOUND | | | | modified 12 Feb 21:56 | 131 MB v1

## evidence_60 — marketing_profile_console.png
OBS | marketing_platform | marketing_001 | id | MARKETING_PROFILE | FOUND | sj.park@naver.com | | +82 10-5837-9451 | last refreshed 15 Sep 2026 23:40 UTC | profile "Soo-jin Park", Live, email opt-in Yes, SMS opt-in No; this identity is in no identity table
