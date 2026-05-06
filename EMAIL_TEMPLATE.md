# Release Verification Email Template

**Subject:** Release Prep: Helping verify our commits for [Release Version]

Hi Team,

I’ve just finished running our automated release audit and a first-pass cherry-pick to help us get ready for the upcoming release. To make sure everything is exactly where it needs to be, I'd really appreciate your help in double-checking the manifest.

Please find the attached **`cherrypick_list.xlsx`**. I’d like everyone to **filter the "Author" column by your name and review every row** assigned to you by **[Insert Time/Date]**.

### 📋 Columns to Review

While you’re looking through your commits, please pay close attention to these three columns:

**1. In Release?** (The current physical state of the code)
*   **Yes**: You're all set! The code is already confirmed in the release branch.
*   **No**: This fix is currently missing from the release branch.
*   **Likely**: The ticket exists in the release, but the code content looks a bit different. **Please verify if your fix is actually there.**
*   **Needs attention**: We found a mismatch in the number of commits. Please check to ensure a partial PR wasn't missed.
*   **Foreign commit touching [File]**: This is a heads-up that someone outside our team touched a file you edited. Please check if your work depends on their changes.

**2. cherry pick?** (Our automation decision)
*   **yes**: This is targeted to be moved.
*   **no**: These were excluded (usually based on the Jira Fix Version). **Please double-check your "no" marks** to make sure we didn't accidentally skip any critical work.
*   **Blank**: These are the ambiguous cases where I need your help deciding if the work should move forward.

**3. success** (Outcome of the initial automated run)
*   I’ve already tried to cherry-pick everything marked "yes" or "blank." 
*   If you see a **"no"** here but the work is required, it means the automated pick hit a conflict and we’ll need to handle it with a manual cherry-pick later.

### 🛠️ Our Goal
By the end of your review, we want to be 100% sure which "No" or "Likely" items still need to land and ensure that our list of "no" omissions is accurate.

Thank you so much for the extra set of eyes on this! Your help is huge in making sure we have a smooth and stable release for everyone. If you have any questions at all, please feel free to message me!

Best regards,

[Your Name]
