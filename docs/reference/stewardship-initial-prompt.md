<!-- pyml disable md005,md007,md009,md012,md022,md024,md025,md033,md041 -->
> **Historical, non-normative source narrative.** The cross-linked stewardship
> specifications contain the resolved requirements and explicitly documented
> overrides. This capture is retained for traceability and is not implementation
> guidance.

After analyzing all of the information below, interview me about
anything that is unclear, gaps in the specification, or anything else
that would prevent you from writing a high-quality, detailed
specification that can be used as the first step of implementing all
the functionality described below.

# Goals

This project is to create one web app to handle all aspects of an
annual Catholic Church stewardship and/or census campaign.  Notable
top-level functionality includes:

* Admin login and configuration

* Repeated periodic Parishioner Member and Family census data and
  Ministry activity refresh from the source of truth (ParishSoft, a
  cloud-based API to pull data from)

* Sending personalized outgoing emails to each Parishioner Family
  containing a link to their Family's stewardship and census data

* Parishioner Family login and data collection.

* Provide interactive visual data reports to staff and administrators
  with progress of the campaign so far

* Post-campaign data resolution and reporting for staff, ministry
  leaders, and administrators

* Final export of data to be updated at ParishSoft

Most of the functionality described here should be a new, and
dedicated to this ParishKit Stewardship/Census Campaign functionality.
But some of it may be useful to be shared functionality, and be
assimilated into general/shared ParishKit functionality.  Ask me if
you are uncertain.

# Roles

There are several roles who will interact with the system:

* Administrator: this role can formally login to the administrative
  web system (via email address), has complete control of the entire
  system.  Administrators can configure anything, see all data, etc.

  Note: users with the "Administrator" (sometimes abbreviated "Admin")
  role automatically implicitly have all other roles.

* Staff: this role can formally login to the administrative web system
  (via email address), and has read-only access to much -- but not all
  -- of the information.

* Ministry leaders: this role can formally login to the administrative
  web system (via email address), and has read-only access to some --
  but not all -- of the information.  Namely: they can see data
  related to their own Ministry(ies).

* Parishioners: this role can login to the parishioner portal web
  system (via a unique 6-character Family code).  They can only see
  and update their own Family's census and Ministry data.

# Technical requirements

* There needs to be unit tests coverage for at least 80% of the code.

* Use other ParishKit common functionality as relevant (e.g.,
  ParishSoft and Google functionality).

* Python is the preferred language.  A full-featured suite like Django
  would be good, or Flask, or something similar.

* All functionality needs to run a Docker compose environment.  The
  build process should create one or more docker images that, during
  the release process, are published to the ParishKit github repo
  registry.

* The images should be able to run in two environments:

  * Locally in a developer environment (Linux, macOS, or Windows host
    with docker available).  No TLS is required in this environment.
    If possible, the containers bind-mount the git repo rather that
    copying in source code to the image, thereby allowing developers
    to make on-the-fly changes in the host that are immediately
    visible to the running container(s).

  * In a production environment (probably a Linux VM running in the
    cloud).  All images should be pulled from the github repo registry
    and then the docker compose environment is launched to run the
    application.  If necessary, setup a reverse proxy on the Linux VM
    so that it can serve the primary web app interface from the
    running container on the VM port 443.  Also setup a Let's Encrypt
    TLS certificate that will auto-renew as necessary.

* In both environments, the back-end storage for the database should
  be durable storage that survives container upgrades and restarts.

  * The LLM can determine an appropriate back-end database.

* All Administrator, Staff, and Ministry leader logins are via
  federated Google login functionality.

* If appropriate, the LLM can pick an IDP system to perform RBAC and
  policy functionality.

* All dates / times will be stored in durable storage as UTC.  These
  dates / times will be converted to the browser-local timezone upon
  display.

## Web UI conventions

The main interaction with the app is through a web UI.

* The entire interface is responsive.

  * The main Administrator, Staff, and Ministry leader usage platform
    will be desktop/laptop, but tablets and phones should be viable as
    well (with admittedly less fidelity of a smaller screen).

  * The main Parishioner family usage platform will likely be mobile
    devices, but some will use desktops/laptops, too.  There should be
    full functionality and fidelity optimized for mobile devices.

* When showing numbers, always include USA conventions of commas.

* When showing "X out of Y" kinds of statistics, include percentages,
  too ("X out of Y (Z%)").

* All "days" will be considered as days in the local timezone of the
  parish (i.e., from 12:00am to 11:59pm).

* The overall system should have a consistent look and feel, style,
  etc.  It should reflect a modern, clean design.


# Overall sequence

The overall sequence of using the system is:

1. First time invocation / initial setup
1. Administrative setup / prepare for the campaign
1. Conduct the campaign
1. Post-campaign data processing


# First time invocation of the system / initial setup

The first time the system is run, it will detect that there is no
configuration present and therefore prompt the administrator (on the
console) for (at least) the following information:

* The name of the parish
* The Google email domain(s) that should be allowed to login with the
  Staff role
* The email address(es) that will be assigned the Admin role
* Any relevant information / credentials that are needed to perform
  federated Google logins
* Any technical, environmental, or other information that is needed
  for proper system operation.

The system will initially be placed in "Testing" mode (described below).

This information should all then be saved in a durable location that
will survive upgrades and compose / container restarts.

# Application Architecture

There are two main components from an end-user perspective:

1. Administration portal.
   1. Interactive activities.
   1. Periodic / background activities.
1. Parishioner portal.


## Administration portal

### Interactive activities

This is where Administrators, Staff, and Ministers can login and
perform functions on the portal.

There are several functions of the Administration Portal:

1. Login (no other Administration functions are available until after login)
1. Viewing background processing
1. Configuration of the system
1. Portal user management
1. Viewing stewardship/census drive activity and progress reports
1. Viewing and exporting reports and data
1. Trigger a manual ParishSoft download
1. Viewing system logs

All functionality for this administration portal is under the /admin/
URL stem.


#### Viewing background processing

For users with the Admin role, there is a visual indicator of two
things:

1. When there are Parishioners actively logged in (i.e., reviewing /
   editing their Family's data).  Clicking on that visual indicator
   will show a list of the Parishioner Family names and DUIDs who are
   currently logged in.

1. When any background periodic tasks are running, show a visual
   indicator of that.  Clicking on the visual indicator should show
   some detail of what is happening in the background.

#### Login

This login page is located at /admin/login.

The only allowable login option is logging in via Google federated
login.

Upon successful Google login, the system is given the email address
associated with the Google account.

The system effectively has two allowlists:

* A list of email domains. If an email address is in any of those
  domains, it is allowed to login to the system.

* A list of specific email addresses. If an email address in the list,
  it is allowed to login to the system.

All other Google accounts will be redirected to a "Sorry, you do not
have access to this system." page and denied access to all other
functionality.

Successful logins will utilize industry-standard session management,
including session adandonment detection, timeout detection, and all
other common best practices.

##### Error pages

All unsuccessful logins (to include failed Google authentication,
failure to match allowlist email domains or addresses, failed RBAC
identification, the system is not yet configured, ... etc.) will allow
attempted re-logging in.

Use industry standard DOS protection for repeated login failures,
including (but not limited to) rate-limiting login attempts, etc.

##### RBAC

If an account's email address is found on either of the allowlists and
allowed to login, it will look up what roles should be assigned to
this session:

* Administrator
* Staff
* Ministry leader

If the session has none of these roles associated with it, it will be
redirected to a "Sorry, you do not have access to the system." page
and denied access to all other functionality.

The Administrator role implicitly contains all other roles.  If a user
has that role assigned, it impliciely has all other roles assigned,
too.

#### Configuration of the system

This functionality is only available to accounts with the
Administrator role.

The following system configuration information is viewable and
editable by the Administrator.  None of these may be empty:

* Parish name
* Parish main web site URL (must be a valid URL)
* Parish local timezone
* Parish phone number (must be a valid US phone number)
* Parish logo graphic(s) (must be a valid graphic file)
  * It may be desireable to have multiple Parish logo graphics for
    different scenarios on the administration portal and the
    parishioner portal.
  * E.g., a favicon, a "small" logo for embedding menus, a "large"
    logo for other situations, etc.
  * If only the "large" logo is provided, extrapolate and create
    the relevant smaller logos and favicon from it.

* Start and end date of the campaign (only one campaign can be running
  at a time).
  * The campain will start at 12:01am on the start date in the local
    timezone of the parish
  * The end date must be after the start date.
  * The campain wille end at 11:59pm on the end date in the local
    timezone of the parish
  * Whether the campaign includes "census" functionality (yes/no)
  * Whether the campaign includes "ministry stewardship" functionality
    (yes/no)
  * Whether the campaign includes "financial stewardship" functionality
    (yes/no): If financial stewardship is enabled:
    * The start and end dates (which must span exactly 1 year -- e.g.,
      January 1 - Dec 31, or July 1 - June 30, etc.) for the time
      period to which that stewardship applies.  For example, a parish
      may have a financial stewardship campaign in October in year X
      that applies to the upcoming January-December in year X+1.
    * A list of "how will Families share" options (see below)
  * At least one of census or stewardships must be "yes".

* Whether the system is in Testing or Production mode

  * When in Testing mode, the system also requires a single email
    address from the Administrator.  And:

    * The system will send *all* outgoing emails to that designated
      testing email address (instead of the usual intended recipient,
      such as Parishioner Families, etc.).

    * Emails will include a prominent visual indicator that the system
      is in "Test" mode and was sent to the test email address rather
      than the intended recipient(s) (and include their names/email
      addresses).

    * The system shall include a prominent visual indicator on all
      pages (for users in the Admin role) indicating that the system
      is in Testing mode (and a link to the relevant Admin
      configuration page where to change the system mode).

  * When in Production mode, none of the Testing mode overrides apply.

* There are multiple blocks, particularly in Parishiner-facing pages,
  where the Parish can provide their own content.  These text sections
  will be listed below.

  * The Administrator can credit, edit, or delete any of these (if no
    content is provided, the system will simply refrain from
    displaying any content in that block).  The system will have a
    WYSIWYG for Admins to edit / update these blocks, and an easy
    system for Admins to understand which block corresponds to which
    Parishioner-facing portion of the web site.  Admins can "preview"
    how each Parishioner-facing web page will look so that they can
    see exactly how their blocks will appear to Parishioners.

* Date / time when to send out the initial emails to Families at the
  start of the campaign
  * Subject for the initial email
  * Text / filename of the template to use for the initial email
    bodies

* Multiple Date / time pairs of when to send out reminder emails to
  Families who have not yet submitted during this campaign
  * Subject for the reminder email
  * Text / filename of the template to use for the reminder email
    bodies

* ParishSoft API key

* Slack credentials (optional).

* Google Workspace email authentication information
* Return email addres to be used for outgoing emails (must be a valid
  email address)

All of this information must be saved in durable backing storage.

##### Initial system setup

There are two phases of setup:

1. During initial system bringup
2. During first administrator login

###### Initial system bringup

At some point during initial system bringup, the system must have some
initial technical configuration defined: an email address that will be
added to the system with the administrator role,
information/credentials for Google federated login, etc.

It may also be desirable to optionally allow restoring data from a
previous database into the current database.

The idea is that the system should obtain whatever basic technical
information it needs to allow the administrator to login on the
administration web portal to complete the next phase of the system setup.

###### First administrator login

When an account with the Administrator role logs in, if the system
configuration does not yet exist, the Administrator is automatically
directed to a wizard-like interface to obtain all of this information.

If the process is aborted before completing, none of the information
is saved and the next time an Administrator account logs in, they are
redirected back to the wizard.

Once the wizard has completed and the configuration is saved, the
Administrator is returned to the main menu.

If a non-Administrator account logs in and the above configuration
does not yet exist, they are redirected to a "Sorry, the system is not
configured yet" page and denied access to all other functionality
until either the configuration information is entered or their session
ends.

All other portions of the system (e.g., Parishioner functionality)
will similarly return a "Sorry, the system is not configured yet"
page + be denied access to all other functionality.


#### Administration portal user management

This functionality is only available to accounts with the Admin role.

This page is where Admins can manage the allowlist of email domains
and specific email addresses that are allowed to login to the
Administration portal via federated Google login.

On the page, there will be 2 tables, each with three columns of
checkboxes:

* Admin
* Staff
* Ministry leader

One table lists allowlisted email domains (sorted by domain), the
other table lists allowlisted email addresses (sorted by email
address).

The role(s) denoted by the columns will be applied to all Google accounts
that either match a domain in the "entire domain" table or match an
email address in the 2nd table.

Changes are saved upon check or uncheck of the checkboxes; there is no
"save" button.  Provide a transient visual indicator indicating that
the check/uncheck has been saved.

The system should read the ParishSoft data and construct a list of all
active Members who currently have the role "Chairperson" in an active
Ministry.  For each of these Members who are not already allowlisted
to login to the list, add them to a list of "Ministers to allowlist"
in the UI.  Include in the listing the Member's name, DUID, and email
address.  There should be an easy UX method for selecting Members from
this list and allowlisting them to be able to login with the Minister
role to the system.

Restrictions:

* The gmail.com domain cannot be allowlisted
* The "Admin" checkboxes should be greyed out or otherwise unavailable
  in the entire-domain allowlist table (i.e., you cannot automatically
  make all members of a given domain be an Administrator)

#### Viewing and exporting reports and data

Staff and Ministers can view several reports.  All graphs should be
interactive -- mousing over should show X/Y values, etc.  Graphs
should be titled, have data legends, labeled axes (with units), etc.
All graphs should also be downloadable, either as a PNG or a PDF.

Each report below indicates which role can see which report.

1. Staff: Parishioner participation in the campaign.
   * X axis is the date
   * Y axis is number of Families who have submitted in this campaign
   * Show (on a single graph):
     * How many Families submitted each day
     * How many Families have submitted since the beginning of the
       campaign
     * On each day, the cumulative total of pledges (if financial
       stewardship is enabled)

1. Staff: Statistics:
   * How many active Families and Members are in the parish
   * How many active Families have an Eligible email addrese in
     ParishSoft (see elsewhere for the definition of Eligible Families)
   * How many active Families have responded in the current campaign
   * Total money pledged so far in the campaign
   * Total money pledged in last year's campaign

1. Staff: A listing of all the "Additional information" text blocks
   submitted by Parishioners.  This list should be searchable and
   filterable by date, Family name, etc.  This report should also
   include a check box that the Admin or Staff member can select to
   indicate that a followup response is needed, and another checkbox
   to indicate that a followup has actually occurred.  Also include a
   text box for notes that Staff members can edit about this
   particular response.  The staff member notes should be durable and
   editable.

1. Staff: A listing of all active Families and their 6-character
   codes.  Have the ability to search for Families' 6-character codes
   by full and partial last name.

   * Note: A Family has exactly one active 6-character code
     (case-insensitive, only letters).  Once a code is generated for a
     Family, it is not changed for the duration of that campaign.

1. Staff: A listing of active Parishioner Families who have no
   Eligible emails.  This report should contain:

   * An interactive sorted listing all such Families (including
     ParishSoft DUIDs).  The list should be searchable and filterable.

   * And exportable/downloadable CSV, XSLX, or PDF file, including the
     Family phone number(s), full snail mail address, etc. so that it
     can be used to generate mailing labels in other software.

1. Staff, Ministers: A summary listing of all Ministry changes (if
   ministry stewardship is enabled):

   * For each Ministry (in a sorted list), a count of how many Members
     have expressed interest in joining that ministry, and a count of
     how many Members have expressed an interest in leaving that
     ministry.

     For each Ministry, there should also be cross-reference links to:

     * A list of all Members indicating interest in joining that
       Ministry, including their ParishSoft DUID, gender, age, phone
       number(s), email address(es), and mailing addresses.

     * A list of all Members indicating their intent to leave the
       Ministry (including their ParishSoft DUID)

     All of these lists should be searchable, sortable, filterable,
     etc., and exportable to a CSV, XLSX, or PDF.

1. Staff, Ministers: A report spanning 1 or more Ministries
   (multi-select, to include an easy option to select all Ministries).
   For each ministry, there should be a CSV, XLSX, PDF page that lists
   the Ministry name, chair names, and ministry stewardship year, and
   then rows with the following columns (if ministry stewardship is
   enabled):

   * Member name
   * Member DUID
   * Member email address(es)
   * Date of email (blank -- to be filled in by a human)
   * Member phone number(s)
   * Date of phone call (blank -- to be filled in by a human)
   * Outcome: Join ministry, no longer interested, or No response

   The intent is that individual pages can be given to ministry
   leaders to print out and follow up with these people.

   Obviously, CSV files cannot include page breaks, so just include a
   blank row and then new column headings, as if there is a page
   break there.

1. Staff: A report showing all pending Member census changes (if
   census stewardship is enabled).

   This list shows only the *changes* that are needed compared to the
   upstream ParishSoft source of truth data.

   The listing should be searchable, sortable, and filterable.  It
   should also visually indicate which items can be updated
   automatically via the ParishSoft API.

   The list should have the option to show the current ParishSoft
   value vs. the changed value.  The list should also have the option
   to omit values that can be automatically updated via the ParishSoft

   The resulting list should also be downloadable as CSV, XLSX, and
   PDF.

#### Viewing system logs

The Admin role users can also view the system logs.  The logs include
everything that this system has done, to include:

* Administrator, Staff, and Minister logins and logouts
* All changes to configuration
* All periodic / background activities
  * Including a listing of each email sent, to whom, and why
* All interactive reports run
* Errors that have occurred
* Parishioner logins
* Parishioner changes to their campaign information

Essentially, the logs should show all notable actions that have
happened, and could essentially be used to "replay" an entire
campaign.

The log viewing screen should be searchable and filterable by strings,
types, dates, etc., and also be exportable/downloadable as text or
structured JSONL.

At least five standard log levels are needed:

1. DEBUG
1. INFO
1. WARNING
1. ERROR
1. CRITICAL
   * A critical error is defined as something that the Admin users
     need to know urgently.

Each level should have its own visual indicator (probably color-coded)
in the log viewing screen.

The log viewing screen should default to filtering out the display of
DEBUG messages.

Similar to other requirements, but worth repeating: all logs will be
captured with UTC timestamps.  But the log viewing screen will render
the timestamps in the local timezone of the web browser.  When
exporting, there will be an option to export in the native UTC
timezone or in the browser-local timezone.

### Periodic / background activities

All periodic / background activities should occur asynchronously,
without impacting the interactive performance of the rest of the
system.

#### Poll ParishSoft for changes

Periodically, the system will use the ParishKit Parishsoft
functionality to poll ParishSoft (the source of truth) for new
information.  In particular, look for changes in Families, Members,
Ministries, membership in Ministries, etc.

ParishKit functionality to load all of this information is a lengthy
operation; it can take some time.  Be prepared to run it
asynchronously.  Be sure to check for errors and re-try if needed.  Do
not run multiple poll-ParishSoft-for-updates operations
simultaneously.

Look for changes in the data fetched from the upstream/source of truth.

*NOTE:* ParishSoft has 2 versions of their API -- v1 and v2.  v2 does
        *not* entirely replace v1 -- there is overlap between v1 and
        v2, but both versions have functionality that the other does
        not.  ParishKit currently supports v2 of the API, but if there
        is functionality in v1 that would be useful, we can build out
        v1 functionality support in ParishKit itself.  Look in the
        parishsoft-api-analysis.md file for more information.
        Specifically, if there's any functionalty that simply checks
        for *changes* at ParishSoft (without having to re-load the
        whole ParishSoft corpus), that might be interesting to
        explore.

Any active parishioner family who does not have a 6-character Family
code should have one generated at this time.

Once fully loaded and processed, the corpus of data loaded from
ParishSoft should now atomically become truth in the system.

*NOTE:* users with the Admin role have another main menu item that
        allows them to trigger a ParishSoft Poll effectively
        immediately (i.e., without waiting for the next periodic
        poll).  This is just a manual trigger in case the Admin knows
        that a change has happened up at ParishSoft that is worth
        reflecting here in the system immediately, rather than waiting
        for the next periodic poll.

#### Send out Family reminder emails

At the appointed scheduled times, the system will send out the emails
to active Parishioners with one or more Eligible Member email
addresses.

Emails are only sent to active parishioner Families.  Specifically:
emails are *not* sent to inactive parishioners, or non-parishioners.

Emails are sent to the Members in an active parishioner Family who are
designated by the `get_family_heads()` ParishKit parishsoft library
function.  This generally returns the adults in a ParishKit Family.
If any of these Members have a valid email address, this email address
is designed as "Eligible", and the Family is therefore designated as
"Able to receive emails".

For each mailing, a single email is sent to each Family, addressed to
all the Eligible email addresses from the Family.

The emails are specific and unique to a given Family.  The email body
is templated, and can substitute values such as:

* Eligible Family Member names
* Family 6-character code
* Family-specific URL that the Families should click on to respond to
  their census/stewardship information (bypassing the prompt for a
  6-character Family code)
* Generic Family URL that will prompt for a 6-character code
* ...any other relevant, Family-specific information

#### Send report emails to administrators

Similar to the information described in the about "Viewing ministry
reports" section, a report will be sent shortly after midnight after
every active day in the campaign containing:

1. Interesting summary statistics from the previous day, presented
   infographic-style

1. The same graph from the "Parishioner participation" web UI report

1. The same statistics from the reports section, presented
   infographic-style.

1. A separate, once-a-week email to Administrators containing a list
   of the "Additional information" text box information submitted by
   Parishioners since the last email was sent out.

#### Errors

All error will be logged in the log (i.e., durable storage).

CRITICAL errors will also be sent to Slack, if Slack credentials were
provided.

CRITICAL errors will also be emailed to the users with the Admin role.

## Parishioner portal

During the campaign start / end dates, Parishioners can login to the
Parishioner Portal.

Before the campaign Parishioners attempting to login will bw shown a
"Sorry, <Parish name>'s census / stewardship campaign will not start
until <date>" kind of message and not be able to use any other
functionality.

Similarly, after the campaign ends, Parishioners will be shown a
"Sorry, <Parish name>'s census / stewardship campaign has ended"
message and not be able to use any other functionality.

### Login

Parishioners login with their 6-character (letters and numbers only,
case-insensitive) case-insensitive unique Family code.

They can enter this code interactively at a login page located at /,
or they can be automatically logged in by passing that 6-character
code as a URL parameter (e.g., by clicking on a Family-specific URL
from a Family-specific email that they received from the system).

If the 6-character code does not map to a valid, active Parishioner
Family, redirect the session to a "Sorry, this family code cannot be
found" kind of error page.

### Reviewing and editing Family information

Once the Family is logged in, they can see if they have already
updated their information during this campaign, and if so, the
time/date when they last submitted.

All information that the Family has changed compared to the ParishSoft
source of truth is indicated in an obvious visual manner.

*NOTE*: Families are unaware of the ParishSoft database; don't use the
        name ParishSoft in Parishioner-facing content.

In general, the all data fields should be pre-filled out / defaulted
with the corresponding data from the ParishSoft source of truth.  Put
differently: a Family who does not need to change any of the data can
simply advance through all the data entry pages and click "Submit" at
the end.

Here are the data that Families can view and edit:

1. View and update Family census information (if census is enabled)
   * Family envelope number (display only -- cannot be edited)
   * Family registration date (display only -- cannot be edited)
   * Home address
   * Mailing address
   * Boolean values:
     * Opt out of *all* emails from the parish

1. For each Member:

  Do each of the following in clearly deleaniated sections (either
  visual panes, pages, sections on a page, or whatever else makes
  sense in the overall theme/look and feel/visual style of the site).

  1. View and update each Member's census information (if census is
     enabled), all are mandatory unless indicated otherwise:
     * First, Middle, and Last Name
     * Name prefix
     * Name suffix
     * Maiden name (optional)
     * Birth date
     * Gender (male, female, or unspecified)
     * Email address (optional)
     * Phone number (optional)
     * Marital status (single, married, divorved)
     * Primary spoken language (English, Spanish, other [text box])

     Also have options for:

     * This person is no longer a member of this Family household
       (e.g., a child who has now become an adult and moved out).
     * This person is now deceased.  Have an (optional) field to enter
       their death date.

  1. Show a list of each Ministry to which that Member belongs (if
     ministry stewardship is enabled):

     * Allow indicating that that Member wishes to stop participating
       in that Ministry
     * Allow indicating that they wish to join other Ministries.
       * Do not allow indicating that a Member wishes to join a
         Ministry in which they already participate.

     Both lists of Ministries may be lengthy; show them in a
     deterministic, sorted order.  It is expected that indicating that
     a Member wishes to join a new Ministry will need to show a long
     list of possible Ministries; only show that list if needed.

1. View and update the Family stewardship information (if financial
   stewardship is enabled)

   * Show the amount that the Family pledged last year (for the
     current financial stewardship period)
   * Show the amount of money that the Family has contributed so far
     (for the current financial stewardship period)
   * Ask how much the Family wants to pledge for the upcoming
     financial stewardship period
   * Ask how the Family wishes to fullfill their pledge:
     * Weekly
     * Monthly
     * Quarterly
     * One annual pledge

     Once the Family indicates this, divide their total pledge by the
     number of time periods (52, 12, or 4) and show them how much they
     should submit for each time period.

     Remind the Family that the pledge / contribution does not take
     effect until the start date of the next financial stewardship
     period (And show them that date).

   * Ask how the Family would like to share (multi-selection):

     For the pronoun, use "I" if the Family consists of exactly 1
     active Member.  Otherwise, use "We".

     These values should be configurable by the Admin as the "How will
     Families Share", briefly mentioned in the Admin configuration
     section.  Here's the default set of options, but which may be
     edited / added to / deleted from by the Admin:

     * I will have my bank send a check (or this is already setup)
     * I will use <Parish name>'s online giving.  <Parish name> may
       update my withdrawl or credit card charge for <year> if needed.
     * I will start to use <Parish name>'s electronic giving.
     * A stock gift to <Parish name>
     * An IRA distribution directly sent to <Parish name>
     * Offertory envelopes (if checked, the parish will have these
       sent to you)
     * Other (text field)

1. If enabled by the Admin, a general "Do you have any additional
   information that you wish to convey to <Parish name>?" open text
   field.

For all data fields with obvious types (e.g., email addresses, dates,
monetary values, etc.), check for the validity of that value upon
navigating out of that text field.  Use an obvious visual indicator on
that field if it is an invalid valid.

When the Family is filling out all the above information, they should
be able to move forward / backwards through the data entry to review,
edit, etc.  The data should not be saved until they have completed
*all* data entry and clicked a definitive "Submit" button at the end.

Once the Family has clicked "Submit", take them to a page that says
"Thank you!" (which will be an Admin-configurable block) and
effectively (silently) logs them out.

Specifically: a Parishioner interaction is generally intended to be a
single flow through the data entry screens and then be complete. This
is not a general dashboard/menu system for Parishioners to login and
continually do multiple actions.  They "login", view/edit/update their
Family's information, and then they are done/"logout".

After a Parishioner hits "Submit" for their Family, they can visit
again to continue to make changes to their information.  The next time
the Parishioner visits, they will see their Family's latest
information from ParishSoft merged with the changes they have made on
prior visit(s).  All changes compared to ParishSoft information will
be visually indicated so that the Parishioner can tell what is "new"
compared to the source of truth.

# Post-campaign data processing

When Census stewardship is active, the Administrator will have a menu
option to both make updates directly to ParishSoft (using the API) and
emit reports about what changes need to be made.

## Make Member updates directly to ParishSoft

For Member and Family elements that can be updated via the ParishSoft
API, the Admin can use a workflow from the system of reviewing each of
the pending Member changes and choosing one of these outcomes for
each:

1. The change is approved, and can be pushed up to ParishSoft
1. The change should be ignored / discarded

Note that the Admin is also allowed to edit the value that will be
pushed up to ParishSoft.  For example: if a Member changes their name
from "John" to "Johnathanxxx", the Admin has the ability to
interactively edit and change the value to "Johnathan" before actually
pushing that change to ParishSoft.

Since there could be dozens (or even hundreds) of changes to be
reviewed, come up with a workflow that allows an Admin to accept (and
edit) / ignore large sets of pending changes at a time.  This also
allows Admins to come back and fix mistakes later (e.g., if they
accidentally marked a pending change as "ignored" but later mark it as
"accepted").

This is just a UX suggestion; I'm open to other, better,
more-industry-standard workflows for tasks like this: perhaps each
change can have an *additional* state:, "not yet reviewed", and that's
the default state for each pending change.  The default workflow only
shows pending changes that are not in the "not yet reviewed" state.
The workflow can show a searchable, filterable, sortable paginated
list, and the default filter can be "only show the pending changes in
the 'not yet reviewed' state."

The workflow should probably just *mark* pending changes for their
outcomes -- actually pushing to ParishSoft should likely happen after
the workflow (perhaps upon the Admin selecting "Publish to
ParishSoft", or somesuch), where the system can get the latest source
of truth from ParishSoft, compute the final changes, and then make API
calls to make the individual changes.  This overall process may take
some time, so it does not need to be during the Admin's review
workflow.

In general: the workflow should be optimized for the Admin's time --
it should be a smooth, intuitive, yet fully functional UX.  Tend to
push things that will take time to execute to the very end of the
workflow (e.g., during "publish").

The Admin should not be forced to review *all* changes in a single
session; they should be able to review *some* changes, potentially
push those changes to ParishSoft, and then logout and come back later
to review additional pending changes.

Note that any "pending change" that now matches the ParishSoft source
of truth no longer needs to show up in the pending changes Admin
workflow.

------------------

After reading all of the above and understanding it, and asking me
questions about anything that you need more clarification on, let's
make a detailed spec for this in docs/specs/stewardship/.  The goal of
this spec will to be a more detailed, comprehensive version of
everything described above such that we can ultimately make an
implementation plan / tasks list from it.
