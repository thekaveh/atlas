#!/bin/bash

ensure_git_repo() {
    local repo_dir="$1"
    local repo_url="$2"

    if [ -z "$repo_dir" ] || [ "$repo_dir" = "/" ]; then
        echo "Local Deep Researcher: ERROR - repository path is unsafe: '$repo_dir'"
        return 1
    fi

    mkdir -p "$repo_dir"
    if ! git -C "$repo_dir" rev-parse --git-dir >/dev/null 2>&1; then
        find "$repo_dir" -mindepth 1 -delete
        git init -q "$repo_dir"
    fi

    if git -C "$repo_dir" remote get-url origin >/dev/null 2>&1; then
        git -C "$repo_dir" remote set-url origin "$repo_url"
    else
        git -C "$repo_dir" remote add origin "$repo_url"
    fi
}

ensure_python_venv() {
    local venv_dir="$1"
    local python_spec="$2"
    local venv_python="$venv_dir/bin/python"

    if [ -z "$venv_dir" ] || [ "$venv_dir" = "/" ]; then
        echo "Local Deep Researcher: ERROR - virtual-environment path is unsafe: '$venv_dir'"
        return 1
    fi

    if [ -x "$venv_python" ] && "$venv_python" -c \
        'import sys; raise SystemExit(sys.version_info[:2] != (3, 11))'; then
        echo "Local Deep Researcher: Reusing healthy Python 3.11 environment"
        return 0
    fi

    echo "Local Deep Researcher: Creating clean Python 3.11 environment"
    uv venv --clear --python "$python_spec" "$venv_dir"
}
