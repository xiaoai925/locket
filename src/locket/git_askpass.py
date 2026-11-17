"""Standalone Git credential callback; no package imports or persisted secrets."""
import os
import sys

if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]).lower()
    key = "LOCKET_GIT_USER" if "username" in prompt else "LOCKET_GIT_TOKEN"
    print(os.environ.get(key, ""))
