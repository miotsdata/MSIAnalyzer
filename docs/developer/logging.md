# Logging

Logging in MSIAnalyzer is an important step to understand whether any problem occurred.

## Principles
There is a configure_logging function (see below) which configure the logger handlers, then each module should create its own logger with `logger = logging.getLogger(__name__)`.

Each function should have at least one logger call.


## Configure Logging

The function [`configure_logging`][msianalyzer.core.utils.logging_utils.configure_logging] is the one used to setup the logger handlers:

- Two for users: stout and optional file
- One for developer: a log file in *src/MSIAnalyzer/log* folder

It should be run in each `main.py` of both CLI and GUI.

## INFO vs DEBUG

The choice of whether a log record should be a INFO or a DEBUG is based on this question:

 *Should the user know about this or is important only for me?*

If the answer is "only for me", then go with DEBUG, otherwise INFO.

## Log format

The log format, as it is now, gives a lot of informations: 

