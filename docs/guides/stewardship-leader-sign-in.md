# Signing in as a Ministry leader

This guide is for Administrators to pass on to Ministry leaders (and Staff) who will use the Stewardship Admin portal, and for operators answering "I can't sign in" ([#1593](https://github.com/epiphany40223/parishkit/issues/1593)). The portal itself shows the same advice in the Users page's About panel, and its "No access with this account" page (where a leader signed in with an address that has no access lands) names the address Google signed them in with; the [login and denial behavior](../specs/stewardship/admin-portal/spec.md#login-and-denial-behavior) specification says where and controls the behavior.

## For the Administrator

- A leader can sign in only after an Administrator adds them as a user, by hand (New user) or with Import on the Users page. Import sends nothing to the leader, so tell each new leader yourself: the portal's address (the Admin sign-in page), which email address you added for them, and the steps below.
- The line under the Users table counts Ministry leaders who have never signed in. Their Last successful sign-in reads Never. A leader who stays there is often stuck at the Google Account step.
- The portal asks Google only for basic sign-in, so Google never shows its "Google hasn't verified this app" page ([the deployment runbook](stewardship-deployment-runbook.md#the-google-hasnt-verified-this-app-page)).
- The portal signs in with Google only. There is no password or emailed sign-in link, and adding one is not planned for v1 (the Administrator can reopen that decision on #1593).

## A Google account with another address

Some people sign in to Google with an address the parish doesn't have, such as a personal Gmail account, while ParishSoft holds their parish or work address. The portal matches people only by the address Google signs them in with. It does not look at a Google Account's other addresses: that needed a sensitive Google permission, and the parish decided against it ([#2387](https://github.com/epiphany40223/parishkit/issues/2387)). To let such a person sign in with the Google account they use:

1. In ParishSoft, add the address they sign in with to their Member record's email field, after the address that is already there, separated by a semicolon: for example `parish.address@example.org; their.gmail@gmail.com`. Use a semicolon, not a comma: ParishSoft separates several addresses only with `;`, and a field with a comma is treated as one invalid address that Stewardship never uses.
2. Add the new address second, never first. The command-line sync tools (Google Groups and Constant Contact) use only the first address in the field, so putting the new one first would move the person's group and list memberships to it.
3. Give the new address its own user on the Users page: each address is a separate user. For a Ministry leader, after the next full ParishSoft refresh the new address is listed as its own row in the table of ParishSoft Ministry leaders without a user, below the Users table, with the same Ministries as their other address: import it. A Staff or Administrator user is added by address with New user, as usual.

Either Google account then signs in with the same access, once its address has a user.

## For the leader

1. Use the email address the parish added for you. It does not have to be a Gmail address: any address works once it has a Google Account.
2. If that address has no Google Account yet, create one for it first, keeping your existing address. On Google's sign-up page, choose to use your existing email address instead of creating a new Gmail address, then confirm the code Google emails to that address. Google's own help is [Create a Google Account](https://support.google.com/accounts/answer/27441).
3. Open the Admin sign-in page, select **Sign in with Google** and pick the Google Account for that address. If you are already signed in to Google with another account (a personal Gmail, say), add or switch to the right one in Google's account chooser.
4. After you sign in, you see the Ministries you lead and their requests. If you see "No access with this account", the page shows the address you signed in with: if the parish added a different address, select **Sign in with a different account** and pick that one; otherwise ask the parish staff member who manages access. "Sign-in didn't finish" means the Google step was cancelled or took too long: try signing in again.

## Troubleshooting for operators

- "Couldn't find your Google Account" on Google's page means the address has no Google Account: step 2.
- "No access with this account" on the portal means Google signed the person in, but with an address that is not a user (or is one whose access was removed; the page does not say which). The page shows the address Google verified: compare it with the address on the Users page. Sign-in activity in System logs usually records such refusals.
- Changing the address means deleting the user and adding the new address (see the Users page's About panel). To let someone sign in with a second Google account instead, see [a Google account with another address](#a-google-account-with-another-address).
