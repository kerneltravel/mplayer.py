# $Id$

"""pymplayer - MPlayer wrapper for Python."""

__version__ = '0.1.0'

__author__ = 'Darwin M. Bautista <djclue917@gmail.com>'

__copyright__ = """
Copyright (C) 2007-2008  The MA3X Project (http://bbs.eee.upd.edu.ph)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""


import re
import socket
import asyncore
import asynchat
from subprocess import Popen, PIPE
from threading import Thread


__all__ = ['MPlayer', 'Server', 'Client', 'PORT', 'MAX_CMD_LENGTH']


_re_cmd_quit = re.compile(r'^(qu?|qui?|quit?)( ?| .*)$', re.IGNORECASE)


PORT = 50001
MAX_CMD_LENGTH = 256


class _ReadableFile(object):
    """Imitates a readable asyncore.dispatcher class.

    This class serves as a wrapper for stdout and stderr
    so that the polling function of asyncore can check them
    for any pending I/O events. The polling function will
    call the handle_read_event method as soon as there is data
    to read.
    """
    def __init__(self, file, map):
        # Add self to map
        map[file.fileno()] = self

    def __getattr__(self, attr):
        # Always return a callable for non-existent attributes.
        # (IMO, this is better than defining all the other asyncore.dispatcher methods)
        return lambda: None

    @staticmethod
    def handle_read_event():
        """
        This method is called by the polling function when
        there is data to read. This will eventually be
        overridden by MPlayer's _handle_data or _handle_error.
        """
        pass

    @staticmethod
    def readable():
        return True

    @staticmethod
    def writable():
        return False


class MPlayer(object):
    """MPlayer(path='mplayer', args=())

    Provides the basic interface for sending commands and receiving
    responses to and from MPlayer. Take note that MPlayer is ALWAYS
    started in 'slave', 'idle', and 'quiet' modes.

    WARNING:
        The MPlayer process would eventually "freeze" if
        the poll_output method is not called because the
        stdout/stderr PIPE buffers would get full.
        Also, the handle_data and handle_error methods
        would only get executed after poll_output is called.

    @property path: path to MPlayer
    @property args: MPlayer arguments
    """
    def __init__(self, path='mplayer', args=()):
        self.path = path
        self.args = args
        self._map = {}
        self.__process = None

    def __del__(self):
        # Be sure to stop the MPlayer process.
        self.stop()

    def _set_path(self, path):
        if not isinstance(path, basestring):
            raise TypeError("path should be a string")
        self.__path = path

    def _get_path(self):
        return self.__path

    path = property(_get_path, _set_path, doc="Path to MPlayer")

    def _get_args(self):
        return self.__args[4:]

    def _set_args(self, args):
        if not isinstance(args, (basestring, list, tuple)):
            raise TypeError("args should either be a string or a tuple or list of strings")
        if isinstance(args, basestring):
            args = args.split()
        elif args:
            for arg in args:
                if not isinstance(arg, basestring):
                    raise TypeError("args should either be a tuple or list of strings")
        self.__args = [self.path, '-slave', '-idle', '-quiet']
        self.__args.extend(args)

    args = property(_get_args, _set_args, doc="MPlayer arguments")

    def _handle_data(self):
        data = self.__process.stdout.readline().rstrip()
        if data:
            self.handle_data(data)

    def _handle_error(self):
        error = self.__process.stderr.readline().rstrip()
        if error:
            self.handle_error(error)

    def poll_output(self, timeout=30.0, use_poll=False):
        """Start asyncore.loop for polling MPlayer's stdout and stderr.

        @param timeout=30.0: timeout parameter for select() or poll()
        @param use_poll=False: use poll() instead of select()

        This method will block unless it is called BEFORE MPlayer is STARTED
        or if the MPlayer process is currently NOT running.

        In a multithreaded app, you may want to spawn a new Thread
        for running this method after calling the start method:

        mplayer = MPlayer()
        mplayer.start()
        thread = threading.Thread(target=mplayer.poll_output)
        thread.setDaemon(True)
        thread.start()
        """
        if not self.isalive() or not self._map:
            return
        asyncore.loop(timeout=timeout, use_poll=use_poll, map=self._map)

    def start(self):
        """Start the MPlayer process.

        Returns True on success, False on failure,
        and None if MPlayer is already running.

        WARNING:
            Don't forget to run the poll_output method
            after calling this method.
        """
        if not self.isalive():
            try:
                # Start subprocess (line-buffered)
                self.__process = Popen(args=self.__args, stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=1)
            except OSError:
                retcode = False
            else:
                _ReadableFile(self.__process.stdout, self._map).handle_read_event = self._handle_data
                _ReadableFile(self.__process.stderr, self._map).handle_read_event = self._handle_error
                retcode = True
            return retcode

    def stop(self):
        """Stop the MPlayer process.

        Returns the exit status of MPlayer or None if not running.
        """
        if self.isalive():
            self.command("quit")
            # Clear the map so that asyncore.loop will terminate
            self._map.clear()
            return self.__process.wait()
        else:
            return None

    def command(self, cmd):
        """Send a command to MPlayer.

        @param cmd: valid MPlayer command

        Valid MPlayer commands are documented in:
        http://www.mplayerhq.hu/DOCS/tech/slave.txt
        """
        if not isinstance(cmd, basestring):
            raise TypeError("command must be a string")
        if not cmd:
            raise ValueError("zero-length command")
        if self.isalive():
            self.__process.stdin.write("".join([cmd, '\n']))

    def isalive(self):
        """Check if MPlayer process is alive.

        Returns True if alive, else, returns False
        """
        try:
            return (self.__process.poll() is None)
        except AttributeError:
            return False

    @staticmethod
    def handle_data(data):
        """This method is meant to be overridden.

        This method is called when a line is read from stdout.

        @param data: the line (str) read from stdout
        """
        pass

    @staticmethod
    def handle_error(error):
        """This method is meant to be overridden.

        This method is called when a line is read from stderr.

        @param error: the line (str) read from stderr
        """
        pass


class _ClientHandler(asynchat.async_chat):
    """Handler of Client connections"""

    ac_in_buffer_size = MAX_CMD_LENGTH
    ac_out_buffer_size = 0

    def __init__(self, mplayer, conn, map):
        asynchat.async_chat.__init__(self, conn)
        self.add_channel(map)
        self._map = map
        self.mplayer = mplayer
        self.buffer = []
        self.set_terminator("\r\n\r\n")

    @staticmethod
    def writable():
        return False

    def handle_close(self):
        self.close()
        self.log("Connection closed: %s" % (self.addr, ))

    def collect_incoming_data(self, data):
        self.buffer.append(data)

    def found_terminator(self):
        cmd = "".join(self.buffer)
        self.buffer = []
        if not cmd or _re_cmd_quit.match(cmd):
            self.handle_close()
        elif cmd.lower() == "reload":
            # (Re)loading a playlist would make MPlayer "jump out" of
            # its XEmbed container, restart the MPlayer process instead.
            # First, remove stdout and stderr from the map;
            map(self._map.pop, self.mplayer._map.keys())
            # then restart the MPlayer process;
            self.mplayer.stop()
            self.mplayer.start()
            # and finally, add stdout and stderr back to the map.
            self._map.update(self.mplayer._map)
        else:
            self.mplayer.command(cmd)


class Server(asyncore.dispatcher):
    """Server(host='', port=pymplayer.PORT, max_conn=1)

    Although this class isn't a subclass of MPlayer, most of
    the MPlayer API and all MPlayer properties are exposed via
    the __getattr__ method. The MPlayer properties function and
    behave properly via some __getattr__ and __setattr__ magic.

    The log method can be overridden to provide more sophisticated
    logging and warning methods.
    """
    def __init__(self, host='', port=PORT, max_conn=1):
        # Use own socket map
        self._map = {}
        asyncore.dispatcher.__init__(self, map=self._map)
        self.__mplayer = MPlayer()
        self.host = host
        self.port = port
        self.max_conn = max_conn
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        self.bind((self.host, self.port))
        self.listen(self.max_conn)

    def __del__(self):
        self.stop()

    def __getattr__(self, attr):
        # Expose MPlayer API (except poll_output) and properties
        if hasattr(self.__mplayer, attr) and attr != 'poll_output':
            return getattr(self.__mplayer, attr)
        else:
            raise AttributeError("'%s' object has no attribute '%s'" % (self.__class__.__name__, attr))

    def __setattr__(self, attr, value):
        # Make the MPlayer properties behave properly
        if attr not in ('args', 'path'):
            self.__dict__[attr] = value
        else:
            setattr(self.__mplayer, attr, value)

    @staticmethod
    def writable():
        return False

    def handle_close(self):
        self.log("Server closed.")
        self.close()

    def handle_accept(self):
        conn, addr = self.accept()
        if len(self._map) - 3 < self.max_conn:
            self.log("Connection accepted: %s" % (addr, ))
            # Dispatch connection to a _ClientHandler and override its log method
            _ClientHandler(self.__mplayer, conn=conn, map=self._map).log = self.log
        else:
            self.log("Max number of connections reached, rejected: %s" % (addr, ))
            conn.close()

    def stop(self):
        """Stop the server.

        Closes all the channels found in self._map (including itself)
        """
        for channel in self._map.values():
            channel.handle_close()
        # The _ReadableFile instances would still remain in self._map
        # Clear map so that asyncore.loop will terminate
        self._map.clear()
        return self.__mplayer.stop()

    def start(self, timeout=30.0, use_poll=False):
        """Start the server.

        @param timeout=30.0: timeout parameter for select() or poll()
        @param use_poll=False: use poll() instead of select()

        Starts the MPlayer process, then enters asyncore.loop (blocking)
        """
        if self.__mplayer.isalive():
            return
        self.__mplayer.start()
        # Include the _ReadableFile instances from self.__mplayer._map
        self._map.update(self.__mplayer._map)
        self.log("Server started.")
        asyncore.loop(timeout=timeout, use_poll=use_poll, map=self._map)


class Client(asynchat.async_chat):
    """Client(host, port=pymplayer.PORT)

    The PyMPlayer Client
    """
    ac_in_buffer_size = 0
    ac_out_buffer_size = MAX_CMD_LENGTH

    def __init__(self, host, port=PORT):
        asynchat.async_chat.__init__(self)
        self.host = host
        self.port = port
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)

    @staticmethod
    def readable():
        return False

    @staticmethod
    def handle_connect():
        pass

    def handle_error(self):
        self.close()
        raise socket.error("Connection lost.")

    def connect(self):
        if self.connected:
            return
        asynchat.async_chat.connect(self, (self.host, self.port))
        t = Thread(target=asyncore.loop)
        t.setDaemon(True)
        t.start()

    def send_command(self, cmd):
        """Send an MPlayer command to the server

        @param cmd: valid MPlayer command
        """
        self.push("".join([cmd, "\r\n\r\n"]))
        if _re_cmd_quit.match(cmd):
            self.close()
            return False
        else:
            return True
