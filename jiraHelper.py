import os, re
from jira import JIRA
from openpyxl import load_workbook
from dotenv import load_dotenv
import config

# Load secrets from .env
load_dotenv()

def extract_jira_id_from_link(link_formula):
    """Extracts the ID from an Excel HYPERLINK formula."""
    if not link_formula or not isinstance(link_formula, str):
        return None
    # Matches HYPERLINK("...","ID") or just ID if it's not a formula
    match = re.search(r'ALP[-_]?(\d+)', link_formula, re.IGNORECASE)
    if match:
        return f"ALP-{match.group(1)}"
    return None

def main():
    # === JIRA AUTHENTICATION ===
    jira_server = config.jira_base_url.split('/browse/')[0]
    jira_user = config.jira_email
    jira_token = config.jira_api_token

    if not jira_user or jira_user == 'your_email@company.com':
        print("❌ Error: jira_email not set correctly in config.py.")
        return

    print(f"Connecting to Jira at {jira_server}...")
    try:
        jira = JIRA(server=jira_server, basic_auth=(jira_user, jira_token))
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # === CONFIGURATION ===
    excel_file = 'cherrypick_list.xlsx'
    if not os.path.exists(excel_file):
        print(f"{excel_file} not found. Run pullRequestHelper.py first.")
        return

    print(f"Reading {excel_file} to look up Fix Versions...")
    
    try:
        wb = load_workbook(excel_file)
        ws = wb.active

        # 1. Find relevant columns
        col_map = {cell.value: cell.column for cell in ws[1]}
        if 'Jira Link' not in col_map:
            print("❌ Error: 'Jira Link' column not found.")
            return

        # Ensure 'Jira Fix Version' column exists
        if 'Jira Fix Version' not in col_map:
            new_col = ws.max_column + 1
            ws.cell(row=1, column=new_col).value = 'Jira Fix Version'
            fix_version_col = new_col
        else:
            fix_version_col = col_map['Jira Fix Version']

        jira_col = col_map['Jira Link']
        
        # 2. Cache lookups to avoid redundant API calls for same ID
        cache = {}

        # 3. Iterate rows (starting at row 2)
        total_rows = ws.max_row - 1
        print(f"Checking {total_rows} rows...")

        for row_idx in range(2, ws.max_row + 1):
            cell_value = ws.cell(row=row_idx, column=jira_col).value
            jira_id = extract_jira_id_from_link(cell_value)

            if jira_id:
                if jira_id in cache:
                    ws.cell(row=row_idx, column=fix_version_col).value = cache[jira_id]
                else:
                    try:
                        print(f"  [{row_idx-1}/{total_rows}] Fetching {jira_id}...", end="\r")
                        issue = jira.issue(jira_id, fields='fixVersions')
                        versions = [v.name for p in [issue.fields.fixVersions] for v in p]
                        version_str = ", ".join(versions) if versions else "No Fix Version"
                        
                        cache[jira_id] = version_str
                        ws.cell(row=row_idx, column=fix_version_col).value = version_str
                    except Exception as e:
                        ws.cell(row=row_idx, column=fix_version_col).value = "Error/Not Found"
            else:
                ws.cell(row=row_idx, column=fix_version_col).value = "N/A"

        wb.save(excel_file)
        print(f"\n✅ Successfully updated {excel_file} with Jira Fix Versions.")

    except Exception as e:
        print(f"\n❌ Error processing Excel: {e}")

if __name__ == "__main__":
    main()
