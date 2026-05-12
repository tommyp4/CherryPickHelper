def get_matched_config_author(repo_name, target_list):
    """Returns the config name if any significant part matches the repo name."""
    repo_name_clean = repo_name.lower()
    for target in target_list:
        target_clean = target.lower().replace(',', ' ')
        parts = [p for p in target_clean.split() if len(p) > 2]
        for p in parts:
            if p in repo_name_clean:
                return target
    return None
