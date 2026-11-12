"""Create or re-sync the Airflow admin user.

The password is read from AIRFLOW_ADMIN_PASSWORD and handed to the FAB user
commands in-process, so it never reaches argv: the `airflow users create
--password` form showed it in `ps` (#1381). users_create leaves an existing user
alone, so user_reset_password then applies the .env password every start.
"""

import os
from argparse import Namespace

from airflow.providers.fab.auth_manager.cli_commands import user_command

ADMIN = "admin"
password = os.environ["AIRFLOW_ADMIN_PASSWORD"]

user_command.users_create(
    Namespace(
        username=ADMIN,
        firstname="Admin",
        lastname="User",
        email="admin@localhost",
        role="Admin",
        password=password,
        use_random_password=False,
    )
)
user_command.user_reset_password(
    Namespace(username=ADMIN, email=None, password=password, use_random_password=False)
)
