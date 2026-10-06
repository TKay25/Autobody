# Working notes for agents in this repo

## Running the test suite (and any long command)

The shell here does **not** return command output reliably — the terminal's
output capture is broken, and a command often reports "completion could not be
observed" while it is in fact still running. So:

1. Redirect to a file, e.g.

       python -m pytest tests -q > full.log 2>&1

2. **Poll that file in a loop until the summary line (`N passed in Ns`, or a
   traceback) appears.** This is a standing rule: repeat the read as many times
   as it takes, back to back, with no cap on how many identical reads are
   issued and no giving up after one or two. The full suite takes roughly 3–4
   minutes and several hundred tests, so a long run of polls is normal.
3. Never treat a shell exit code as evidence of success or failure here — the
   file's contents are the only truth. Never assume a command succeeded just
   because the tool call returned.
4. Delete scratch log files when the task is done.
