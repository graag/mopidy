from __future__ import absolute_import, unicode_literals

import logging
import logging.config
import logging.handlers
import platform


LOG_LEVELS = {
    -1: dict(root=logging.ERROR, mopidy=logging.WARNING),
    0: dict(root=logging.ERROR, mopidy=logging.INFO),
    1: dict(root=logging.WARNING, mopidy=logging.DEBUG),
    2: dict(root=logging.INFO, mopidy=logging.DEBUG),
    3: dict(root=logging.DEBUG, mopidy=logging.DEBUG),
    4: dict(root=logging.NOTSET, mopidy=logging.NOTSET),
}

# Custom log level which has even lower priority than DEBUG
TRACE_LOG_LEVEL = 5
logging.addLevelName(TRACE_LOG_LEVEL, 'TRACE')

logger = logging.getLogger(__name__)


class DelayedHandler(logging.Handler):

    def __init__(self):
        logging.Handler.__init__(self)
        self._released = False
        self._buffer = []

    def handle(self, record):
        if not self._released:
            self._buffer.append(record)

    def release(self):
        self._released = True
        root = logging.getLogger('')
        while self._buffer:
            root.handle(self._buffer.pop(0))


_delayed_handler = DelayedHandler()


def bootstrap_delayed_logging():
    root = logging.getLogger('')
    root.setLevel(logging.NOTSET)
    root.addHandler(_delayed_handler)


def setup_logging(config, base_verbosity_level, args_verbosity_level):
    logging.captureWarnings(True)

    if config['logging']['config_file']:
        # Logging config from file must be read before other handlers are
        # added. If not, the other handlers will have no effect.
        try:
            path = config['logging']['config_file']
            logging.config.fileConfig(path, disable_existing_loggers=False)
        except Exception as e:
            # Catch everything as logging does not specify what can go wrong.
            logger.error('Loading logging config %r failed. %s', path, e)

    loglevels = config.get('loglevels', {})

    verbosity_level = get_verbosity_level(
        config, base_verbosity_level, args_verbosity_level)
    verbosity_filter = VerbosityFilter(verbosity_level, loglevels)

    formatter = logging.Formatter(config['logging']['format'])

    if config['logging']['color']:
        handler = ColorizingStreamHandler(config.get('logcolors', {}))
    else:
        handler = logging.StreamHandler()
    handler.addFilter(verbosity_filter)
    handler.setFormatter(formatter)

    logging.getLogger('').addHandler(handler)

    _delayed_handler.release()


def get_verbosity_level(config, base_verbosity_level, args_verbosity_level):
    if args_verbosity_level:
        result = base_verbosity_level + args_verbosity_level
    else:
        result = base_verbosity_level + config['logging']['verbosity']

    if result < min(LOG_LEVELS.keys()):
        result = min(LOG_LEVELS.keys())
    if result > max(LOG_LEVELS.keys()):
        result = max(LOG_LEVELS.keys())

    return result


class VerbosityFilter(logging.Filter):

    def __init__(self, verbosity_level, loglevels):
        self.verbosity_level = verbosity_level
        self.loglevels = loglevels

    def filter(self, record):
        for name, required_log_level in self.loglevels.items():
            if record.name == name or record.name.startswith(name + '.'):
                return record.levelno >= required_log_level

        if record.name.startswith('mopidy'):
            required_log_level = LOG_LEVELS[self.verbosity_level]['mopidy']
        else:
            required_log_level = LOG_LEVELS[self.verbosity_level]['root']
        return record.levelno >= required_log_level


#: Available log colors.
COLORS = [
    'black',
    'red',
    'green',
    'yellow',
    'blue',
    'magenta',
    'cyan',
    'white',
]


class ColorizingStreamHandler(logging.StreamHandler):

    """
    Stream handler which colorizes the log using ANSI escape sequences.

    Does nothing on Windows, which doesn't support ANSI escape sequences.

    This implementation is based upon https://gist.github.com/vsajip/758430,
    which is:

        Copyright (C) 2010-2012 Vinay Sajip. All rights reserved.
        Licensed under the new BSD license.
    """

    # Map logging levels to (background, foreground, bold/intense)
    level_map = {
        TRACE_LOG_LEVEL: (None, 'blue', False),
        logging.DEBUG: (None, 'blue', False),
        logging.INFO: (None, 'white', False),
        logging.WARNING: (None, 'yellow', False),
        logging.ERROR: (None, 'red', False),
        logging.CRITICAL: ('red', 'white', True),
    }
    # Map logger name to foreground colors
    logger_map = {}

    csi = '\x1b['
    reset = '\x1b[0m'

    is_windows = platform.system() == 'Windows'

    def __init__(self, logger_colors):
        super(ColorizingStreamHandler, self).__init__()
        self.logger_map = logger_colors

    @property
    def is_tty(self):
        isatty = getattr(self.stream, 'isatty', None)
        return isatty and isatty()

    def emit(self, record):
        try:
            message = self.format(record)
            self.stream.write(message)
            self.stream.write(getattr(self, 'terminator', '\n'))
            self.flush()
        except Exception:
            self.handleError(record)

    def format(self, record):
        message = logging.StreamHandler.format(self, record)
        if not self.is_tty or self.is_windows:
            return message
        for name, color in self.logger_map.iteritems():
            if record.name.startswith(name):
                return self.colorize(message, fg=color)
        if record.levelno in self.level_map:
            bg, fg, bold = self.level_map[record.levelno]
            return self.colorize(message, bg=bg, fg=fg, bold=bold)
        return message

    def colorize(self, message, bg=None, fg=None, bold=False):
        params = []
        if bg in COLORS:
            params.append(str(COLORS.index(bg) + 40))
        if fg in COLORS:
            params.append(str(COLORS.index(fg) + 30))
        if bold:
            params.append('1')
        if params:
            message = ''.join((
                self.csi, ';'.join(params), 'm', message, self.reset))
        return message
