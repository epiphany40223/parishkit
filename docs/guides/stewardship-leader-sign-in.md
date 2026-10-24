# Signing in as a Ministry leader

This guide is for Administrators to pass on to Ministry leaders (and Staff) who will use the Stewardship Admin portal, and for operators answering "I can't sign in" ([#1593](https://github.com/epiphany40223/parishkit/issues/1593)). The portal itself shows the same advice on its refusal page (where a leader lands when sign-in fails) and in the Users page's About panel; the [login and denial behavior](../specs/stewardship/admin-portal/spec.md#login-and-denial-behavior) specification says where and controls the behavior.

## For the Administrator

- A leader can sign in only after an Administrator adds them as a user, by hand (New user) or with Import on the Users page. Import sends nothing to the leader, so tell each new leader yourself: the portal's address (the Admin sign-in page), which email address you added for them, and the steps below.
- The line under the Users table counts Ministry leaders who have never signed in. Their Last successful sign-in reads Never. A leader who stays there is often stuck at the Google Account step.
- The portal signs in with Google only. There is no password or emailed sign-in link, and adding one is not planned for v1 (the Administrator can reopen that decision on #1593).

## For the leader

1. Use the email address the parish added for you. It does not have to be a Gmail address: any address works once it has a Google Account.
2. If that address has no Google Account yet, create one for it first, keeping your existing address. On Google's sign-up page, choose to use your existing email address instead of creating a new Gmail address, then confirm the code Google emails to that address. Google's own help is [Create a Google Account](https://support.google.com/accounts/answer/27441).
3. Open the Admin sign-in page, select **Sign in with Google** and pick the Google Account for that address. If you are already signed in to Google with another account (a personal Gmail, say), add or switch to the right one in Google's account chooser.
4. After you sign in, you see the Ministries you lead and their requests. If you see "Access unavailable", you are signed in with a Google Account for a different address, or the parish has not added your address yet: try again with the right account, or ask the parish staff member who manages access.

## Troubleshooting for operators

- "Couldn't find your Google Account" on Google's page means the address has no Google Account: step 2.
- "Access unavailable" on the portal usually means Google signed the person in, but with an address that is not a user. Compare the address on the Users page with the one the person chose in Google's account chooser. Sign-in activity in System logs usually records such refusals.
- Changing the address means deleting the user and adding the new address (see the Users page's About panel).
