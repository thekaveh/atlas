# Temporary Build-Validation Gate

## 1. Context

The `Build-validation (Dockerfile + requirements.txt installability)` job builds
15 local images on every eligible pull request. Recent successful runs spend
about 22 minutes in this job while the other required checks finish in roughly
one to six minutes.

## 2. Design

Keep the job definition intact but gate it with the repository Actions variable
`ENABLE_BUILD_VALIDATION`. The job runs only when the variable is exactly
`true`. Remove its status context from the protected `gitflow` ruleset while
the gate is disabled; retain the other three required checks and strict mode.

## 3. Re-enabling

1. Set `ENABLE_BUILD_VALIDATION=true` in the repository Actions variables.
2. Add `Build-validation (Dockerfile + requirements.txt installability)` back
   to the `gitflow` ruleset's required status checks.
3. Open a pull request and verify that all four checks run and pass.

No Dockerfiles, dependency pins, test behavior, or runtime configuration change.
