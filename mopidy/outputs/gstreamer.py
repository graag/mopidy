import pygst
pygst.require('0.10')
import gst

import logging

from pykka.actor import ThreadingActor
from pykka.registry import ActorRegistry

from mopidy import settings
from mopidy.backends.base import Backend
from mopidy.outputs.base import BaseOutput

logger = logging.getLogger('mopidy.outputs.gstreamer')

class GStreamerOutput(ThreadingActor, BaseOutput):
    """
    Audio output through `GStreamer <http://gstreamer.freedesktop.org/>`_.

    **Settings:**

    - :attr:`mopidy.settings.GSTREAMER_AUDIO_SINK`

    """

    def __init__(self):
        self.gst_pipeline = None

    def pre_start(self):
        self._setup_gstreamer()

    def _setup_gstreamer(self):
        """
        **Warning:** :class:`GStreamerOutput` requires
        :class:`mopidy.utils.process.GObjectEventThread` to be running. This is
        not enforced by :class:`GStreamerOutput` itself.
        """

        logger.debug(u'Setting up GStreamer pipeline')

        self.gst_pipeline = gst.parse_launch(' ! '.join([
            'audioconvert name=convert',
            'volume name=volume',
            settings.GSTREAMER_AUDIO_SINK,
        ]))

        pad = self.gst_pipeline.get_by_name('convert').get_pad('sink')

        if settings.BACKENDS[0] == 'mopidy.backends.local.LocalBackend':
            uri_bin = gst.element_factory_make('uridecodebin', 'uri')
            uri_bin.connect('pad-added', self._process_new_pad, pad)
            self.gst_pipeline.add(uri_bin)
        else:
            app_src = gst.element_factory_make('appsrc', 'appsrc')
            app_src_caps = gst.Caps("""
                audio/x-raw-int,
                endianness=(int)1234,
                channels=(int)2,
                width=(int)16,
                depth=(int)16,
                signed=(boolean)true,
                rate=(int)44100""")
            app_src.set_property('caps', app_src_caps)
            self.gst_pipeline.add(app_src)
            app_src.get_pad('src').link(pad)

        # Setup bus and message processor
        gst_bus = self.gst_pipeline.get_bus()
        gst_bus.add_signal_watch()
        gst_bus.connect('message', self._process_gstreamer_message)

    def _process_new_pad(self, source, pad, target_pad):
        pad.link(target_pad)

    def _process_gstreamer_message(self, bus, message):
        """Process messages from GStreamer."""
        if message.type == gst.MESSAGE_EOS:
            logger.debug(u'GStreamer signalled end-of-stream. '
                'Telling backend ...')
            self._get_backend().playback.on_end_of_track()
        elif message.type == gst.MESSAGE_ERROR:
            self.set_state('NULL')
            error, debug = message.parse_error()
            logger.error(u'%s %s', error, debug)
            # FIXME Should we send 'stop_playback' to the backend here? Can we
            # differentiate on how serious the error is?

    def _get_backend(self):
        backend_refs = ActorRegistry.get_by_class(Backend)
        assert len(backend_refs) == 1, 'Expected exactly one running backend.'
        return backend_refs[0].proxy()

    def play_uri(self, uri):
        """Play audio at URI"""
        self.set_state('READY')
        self.gst_pipeline.get_by_name('uri').set_property('uri', uri)
        return self.set_state('PLAYING')

    def deliver_data(self, caps_string, data):
        """Deliver audio data to be played"""
        app_src = self.gst_pipeline.get_by_name('appsrc')
        caps = gst.caps_from_string(caps_string)
        buffer_ = gst.Buffer(buffer(data))
        buffer_.set_caps(caps)
        app_src.set_property('caps', caps)
        app_src.emit('push-buffer', buffer_)

    def end_of_data_stream(self):
        """
        Add end-of-stream token to source.

        We will get a GStreamer message when the stream playback reaches the
        token, and can then do any end-of-stream related tasks.
        """
        self.gst_pipeline.get_by_name('appsrc').emit('end-of-stream')

    def get_position(self):
        try:
            position = self.gst_pipeline.query_position(gst.FORMAT_TIME)[0]
            return position // gst.MSECOND
        except gst.QueryError, e:
            logger.error('time_position failed: %s', e)
            return 0

    def set_position(self, position):
        self.gst_pipeline.get_state() # block until state changes are done
        handeled = self.gst_pipeline.seek_simple(gst.Format(gst.FORMAT_TIME),
            gst.SEEK_FLAG_FLUSH, position * gst.MSECOND)
        self.gst_pipeline.get_state() # block until seek is done
        return handeled

    def set_state(self, state_name):
        """
        Set the GStreamer state. Returns :class:`True` if successful.

        .. digraph:: gst_state_transitions

            "NULL" -> "READY"
            "PAUSED" -> "PLAYING"
            "PAUSED" -> "READY"
            "PLAYING" -> "PAUSED"
            "READY" -> "NULL"
            "READY" -> "PAUSED"

        :param state_name: NULL, READY, PAUSED, or PLAYING
        :type state_name: string
        :rtype: :class:`True` or :class:`False`
        """
        result = self.gst_pipeline.set_state(
            getattr(gst, 'STATE_' + state_name))
        if result == gst.STATE_CHANGE_FAILURE:
            logger.warning('Setting GStreamer state to %s: failed', state_name)
            return False
        else:
            logger.debug('Setting GStreamer state to %s: OK', state_name)
            return True

    def get_volume(self):
        """Get volume in range [0..100]"""
        gst_volume = self.gst_pipeline.get_by_name('volume')
        return int(gst_volume.get_property('volume') * 100)

    def set_volume(self, volume):
        """Set volume in range [0..100]"""
        gst_volume = self.gst_pipeline.get_by_name('volume')
        gst_volume.set_property('volume', volume / 100.0)
        return True
