"""
User interface Controls for the layout.
"""
from __future__ import unicode_literals

from abc import ABCMeta, abstractmethod
from collections import namedtuple
from six import with_metaclass
from six.moves import range

from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.cache import SimpleCache
from prompt_toolkit.filters import to_filter
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines, fragment_list_to_text
from prompt_toolkit.lexers import Lexer, SimpleLexer
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.search import SearchState
from prompt_toolkit.selection import SelectionType
from prompt_toolkit.utils import get_cwidth

from .processors import TransformationInput, HighlightSearchProcessor, HighlightIncrementalSearchProcessor, HighlightSelectionProcessor, DisplayMultipleCursors, merge_processors
from .screen import Point

import six
import time


__all__ = [
    'BufferControl',
    'SearchBufferControl',
    'DummyControl',
    'FormattedTextControl',
    'UIControl',
    'UIContent',
]


class UIControl(with_metaclass(ABCMeta, object)):
    """
    Base class for all user interface controls.
    """
    def reset(self):
        # Default reset. (Doesn't have to be implemented.)
        pass

    def preferred_width(self, max_available_width):
        return None

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        return None

    def is_focusable(self):
        """
        Tell whether this user control is focusable.
        """
        return False

    @abstractmethod
    def create_content(self, width, height):
        """
        Generate the content for this user control.

        Returns a :class:`.UIContent` instance.
        """

    def mouse_handler(self, mouse_event):
        """
        Handle mouse events.

        When `NotImplemented` is returned, it means that the given event is not
        handled by the `UIControl` itself. The `Window` or key bindings can
        decide to handle this event as scrolling or changing focus.

        :param mouse_event: `MouseEvent` instance.
        """
        return NotImplemented

    def move_cursor_down(self):
        """
        Request to move the cursor down.
        This happens when scrolling down and the cursor is completely at the
        top.
        """

    def move_cursor_up(self):
        """
        Request to move the cursor up.
        """

    def get_key_bindings(self):
        """
        The key bindings that are specific for this user control.

        Return a :class:`.KeyBindings` object if some key bindings are
        specified, or `None` otherwise.
        """

    def get_invalidate_events(self):
        """
        Return a list of `Event` objects. This can be a generator.
        (The application collects all these events, in order to bind redraw
        handlers to these events.)
        """
        return []


class UIContent(object):
    """
    Content generated by a user control. This content consists of a list of
    lines.

    :param get_line: Callable that takes a line number and returns the current
        line. This is a list of (style_str, text) tuples.
    :param line_count: The number of lines.
    :param cursor_position: a :class:`.Point` for the cursor position.
    :param menu_position: a :class:`.Point` for the menu position.
    :param show_cursor: Make the cursor visible.
    """
    def __init__(self, get_line=None, line_count=0,
                 cursor_position=None, menu_position=None, show_cursor=True):
        assert callable(get_line)
        assert isinstance(line_count, six.integer_types)
        assert cursor_position is None or isinstance(cursor_position, Point)
        assert menu_position is None or isinstance(menu_position, Point)

        self.get_line = get_line
        self.line_count = line_count
        self.cursor_position = cursor_position or Point(x=0, y=0)
        self.menu_position = menu_position
        self.show_cursor = show_cursor

        # Cache for line heights. Maps (lineno, width) -> height.
        self._line_heights = {}

    def __getitem__(self, lineno):
        " Make it iterable (iterate line by line). "
        if lineno < self.line_count:
            return self.get_line(lineno)
        else:
            raise IndexError

    def get_height_for_line(self, lineno, width, get_line_prefix):

                # TODO: this should also return the newly created text (including the prefix).
                #       we need that for computing the scroll offset, and
                #       better not to compute twice.

                # TODO: move this code into "Window" somewhere???
        """
        Return the height that a given line would need if it is rendered in a
        space with the given width (using line wrapping).

        :param get_line_prefix: None or a `Window.get_line_prefix` callable
            that returns the prefix to be inserted before this line.
        """
                     # TODO: maybe if no prefix is given, use the fast path
                     #       that we had before with the function
                     #       below!!!!!!!!!!!!!!!!
        if get_line_prefix is None:
            get_line_prefix = lambda *a: []

        # Instead of using `get_line_prefix` as key, we use render_counter
        # instead. This is more reliable, because this function could still be
        # the same, while the content would change over time.
        key = get_app().render_counter, lineno, width

        try:
            return self._line_heights[key]
        except KeyError:
            if width == 0:
                height = 10 ** 8
            else:
                # Calculate text width first.
                text = fragment_list_to_text(self.get_line(lineno))
                text_width = get_cwidth(text)

                # Add prefix of this line.
                prefix_width = get_cwidth(fragment_list_to_text(to_formatted_text(
                    get_line_prefix(width, lineno, False))))
                text_width += prefix_width

                # Keep wrapping as long as the line doesn't fit.
                # Keep adding new prefixes for every wrapped line.
                height = 1

                while text_width > width:
                    height += 1
                    text_width -= width

                    prefix_text = fragment_list_to_text(to_formatted_text(
                        get_line_prefix(width, lineno, True)))
                    prefix_width = get_cwidth(prefix_text)

                    if prefix_width > width:  # Prefix doesn't fit.
                        height = 10 ** 8
                        break

                    text_width += prefix_width

            # Cache and return
            self._line_heights[key] = height
            return height

    @staticmethod
    def get_height_for_text(text, width):
        # Get text width for this line.
        line_width = get_cwidth(text)

        # Calculate height.
        try:
            quotient, remainder = divmod(line_width, width)
        except ZeroDivisionError:
            # Return something very big.
            # (This can happen, when the Window gets very small.)
            return 10 ** 8
        else:
            if remainder:
                quotient += 1  # Like math.ceil.
            return max(1, quotient)


class FormattedTextControl(UIControl):
    """
    Control that displays formatted text. This can be either plain text, an
    :class:`~prompt_toolkit.formatted_text.HTML` object an
    :class:`~prompt_toolkit.formatted_text.ANSI` object or a list of
    ``(style_str, text)`` tuples, depending on how you prefer to do the
    formatting. See ``prompt_toolkit.layout.formatted_text`` for more
    information.

    (It's mostly optimized for rather small widgets, like toolbars, menus, etc...)

    When this UI control has the focus, the cursor will be shown in the upper
    left corner of this control by default. There are two ways for specifying
    the cursor position:

    - Pass a `get_cursor_position` function which returns a `Point` instance
      with the current cursor position.

    - If the (formatted) text is passed as a list of ``(style, text)`` tuples
      and there is one that looks like ``('[SetCursorPosition]', '')``, then
      this will specify the cursor position.

    Mouse support:

        The list of fragments can also contain tuples of three items, looking like:
        (style_str, text, handler). When mouse support is enabled and the user
        clicks on this fragment, then the given handler is called. That handler
        should accept two inputs: (Application, MouseEvent) and it should
        either handle the event or return `NotImplemented` in case we want the
        containing Window to handle this event.

    :param focusable: `bool` or :class:`.Filter`: Tell whether this control is
        focusable.

    :param text: Text or formatted text to be displayed.
    :param style: Style string applied to the content. (If you want to style
        the whole :class:`~prompt_toolkit.layout.Window`, pass the style to the
        :class:`~prompt_toolkit.layout.Window` instead.)
    :param key_bindings: a :class:`.KeyBindings` object.
    :param get_cursor_position: A callable that returns the cursor position as
        a `Point` instance.
    """
    def __init__(self, text='', style='', focusable=False, key_bindings=None,
                 show_cursor=True, modal=False, get_cursor_position=None):
        from prompt_toolkit.key_binding.key_bindings import KeyBindingsBase
        assert isinstance(style, six.text_type)
        assert key_bindings is None or isinstance(key_bindings, KeyBindingsBase)
        assert isinstance(show_cursor, bool)
        assert isinstance(modal, bool)
        assert get_cursor_position is None or callable(get_cursor_position)

        self.text = text  # No type check on 'text'. This is done dynamically.
        self.style = style
        self.focusable = to_filter(focusable)

        # Key bindings.
        self.key_bindings = key_bindings
        self.show_cursor = show_cursor
        self.modal = modal
        self.get_cursor_position = get_cursor_position

        #: Cache for the content.
        self._content_cache = SimpleCache(maxsize=18)
        self._fragment_cache = SimpleCache(maxsize=1)
            # Only cache one fragment list. We don't need the previous item.

        # Render info for the mouse support.
        self._fragments = None

    def reset(self):
        self._fragments = None

    def is_focusable(self):
        return self.focusable()

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self.text)

    def _get_formatted_text_cached(self):
        """
        Get fragments, but only retrieve fragments once during one render run.
        (This function is called several times during one rendering, because
        we also need those for calculating the dimensions.)
        """
        return self._fragment_cache.get(
            get_app().render_counter,
            lambda: to_formatted_text(self.text, self.style))

    def preferred_width(self, max_available_width):
        """
        Return the preferred width for this control.
        That is the width of the longest line.
        """
        text = fragment_list_to_text(self._get_formatted_text_cached())
        line_lengths = [get_cwidth(l) for l in text.split('\n')]
        return max(line_lengths)

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        content = self.create_content(width, None)
        return content.line_count

    def create_content(self, width, height):
        # Get fragments
        fragments_with_mouse_handlers = self._get_formatted_text_cached()
        fragment_lines_with_mouse_handlers = list(split_lines(fragments_with_mouse_handlers))

        # Strip mouse handlers from fragments.
        fragment_lines = [
            [tuple(item[:2]) for item in line]
            for line in fragment_lines_with_mouse_handlers
        ]

        # Keep track of the fragments with mouse handler, for later use in
        # `mouse_handler`.
        self._fragments = fragments_with_mouse_handlers

        # If there is a `[SetCursorPosition]` in the fragment list, set the
        # cursor position here.
        def get_cursor_position(fragment='[SetCursorPosition]'):
            for y, line in enumerate(fragment_lines):
                x = 0
                for style_str, text in line:
                    if fragment in style_str:
                        return Point(x=x, y=y)
                    x += len(text)
            return None

        # If there is a `[SetMenuPosition]`, set the menu over here.
        def get_menu_position():
            return get_cursor_position('[SetMenuPosition]')

        cursor_position = (self.get_cursor_position or get_cursor_position)()

        # Create content, or take it from the cache.
        key = (tuple(fragments_with_mouse_handlers), width, cursor_position)

        def get_content():
            return UIContent(get_line=lambda i: fragment_lines[i],
                             line_count=len(fragment_lines),
                             show_cursor=self.show_cursor,
                             cursor_position=cursor_position,
                             menu_position=get_menu_position())

        return self._content_cache.get(key, get_content)

    def mouse_handler(self, mouse_event):
        """
        Handle mouse events.

        (When the fragment list contained mouse handlers and the user clicked on
        on any of these, the matching handler is called. This handler can still
        return `NotImplemented` in case we want the
        :class:`~prompt_toolkit.layout.Window` to handle this particular
        event.)
        """
        if self._fragments:
            # Read the generator.
            fragments_for_line = list(split_lines(self._fragments))

            try:
                fragments = fragments_for_line[mouse_event.position.y]
            except IndexError:
                return NotImplemented
            else:
                # Find position in the fragment list.
                xpos = mouse_event.position.x

                # Find mouse handler for this character.
                count = 0
                for item in fragments:
                    count += len(item[1])
                    if count >= xpos:
                        if len(item) >= 3:
                            # Handler found. Call it.
                            # (Handler can return NotImplemented, so return
                            # that result.)
                            handler = item[2]
                            return handler(mouse_event)
                        else:
                            break

        # Otherwise, don't handle here.
        return NotImplemented

    def is_modal(self):
        return self.modal

    def get_key_bindings(self):
        return self.key_bindings


class DummyControl(UIControl):
    """
    A dummy control object that doesn't paint any content.

    Useful for filling a :class:`~prompt_toolkit.layout.Window`. (The
    `fragment` and `char` attributes of the `Window` class can be used to
    define the filling.)
    """
    def create_content(self, width, height):
        def get_line(i):
            return []

        return UIContent(
            get_line=get_line,
            line_count=100 ** 100)  # Something very big.

    def is_focusable(self):
        return False


_ProcessedLine = namedtuple('_ProcessedLine', 'fragments source_to_display display_to_source')


class BufferControl(UIControl):
    """
    Control for visualising the content of a :class:`.Buffer`.

    :param buffer: The :class:`.Buffer` object to be displayed.
    :param input_processors: A list of
        :class:`~prompt_toolkit.layout.processors.Processor` objects.
    :param include_default_input_processors: When True, include the default
        processors for highlighting of selection, search and displaying of
        multiple cursors.
    :param lexer: :class:`.Lexer` instance for syntax highlighting.
    :param preview_search: `bool` or :class:`.Filter`: Show search while
        typing. When this is `True`, probably you want to add a
        ``HighlightIncrementalSearchProcessor`` as well. Otherwise only the
        cursor position will move, but the text won't be highlighted.
    :param focusable: `bool` or :class:`.Filter`: Tell whether this control is focusable.
    :param focus_on_click: Focus this buffer when it's click, but not yet focused.
    :param key_bindings: a :class:`.KeyBindings` object.
    """
    def __init__(self,
                 buffer=None,
                 input_processors=None,
                 include_default_input_processors=True,
                 lexer=None,
                 preview_search=False,
                 focusable=True,
                 search_buffer_control=None,
                 menu_position=None,
                 focus_on_click=False,
                 key_bindings=None):
        from prompt_toolkit.key_binding.key_bindings import KeyBindingsBase
        assert buffer is None or isinstance(buffer, Buffer)
        assert input_processors is None or isinstance(input_processors, list)
        assert isinstance(include_default_input_processors, bool)
        assert menu_position is None or callable(menu_position)
        assert lexer is None or isinstance(lexer, Lexer), 'Got %r' % (lexer, )
        assert (search_buffer_control is None or
                callable(search_buffer_control) or
                isinstance(search_buffer_control, SearchBufferControl))
        assert key_bindings is None or isinstance(key_bindings, KeyBindingsBase)

        self.input_processors = input_processors
        self.include_default_input_processors = include_default_input_processors

        self.default_input_processors = [
            HighlightSearchProcessor(),
            HighlightIncrementalSearchProcessor(),
            HighlightSelectionProcessor(),
            DisplayMultipleCursors(),
        ]

        self.preview_search = to_filter(preview_search)
        self.focusable = to_filter(focusable)
        self.focus_on_click = to_filter(focus_on_click)

        self.buffer = buffer or Buffer()
        self.menu_position = menu_position
        self.lexer = lexer or SimpleLexer()
        self.key_bindings = key_bindings
        self._search_buffer_control = search_buffer_control

        #: Cache for the lexer.
        #: Often, due to cursor movement, undo/redo and window resizing
        #: operations, it happens that a short time, the same document has to be
        #: lexed. This is a fairly easy way to cache such an expensive operation.
        self._fragment_cache = SimpleCache(maxsize=8)

        self._xy_to_cursor_position = None
        self._last_click_timestamp = None
        self._last_get_processed_line = None

    def __repr__(self):
        return '<%s buffer=%r at %r>' % (self.__class__.__name__, self.buffer, id(self))

    @property
    def search_buffer_control(self):
        if callable(self._search_buffer_control):
            result = self._search_buffer_control()
        else:
            result = self._search_buffer_control

        assert result is None or isinstance(result, SearchBufferControl)
        return result

    @property
    def search_buffer(self):
        control = self.search_buffer_control
        if control is not None:
            return control.buffer

    @property
    def search_state(self):
        """
        Return the `SearchState` for searching this `BufferControl`. This is
        always associated with the search control. If one search bar is used
        for searching multiple `BufferControls`, then they share the same
        `SearchState`.
        """
        search_buffer_control = self.search_buffer_control
        if search_buffer_control:
            return search_buffer_control.searcher_search_state
        else:
            return SearchState()

    def is_focusable(self):
        return self.focusable()

    def preferred_width(self, max_available_width):
        """
        This should return the preferred width.

        Note: We don't specify a preferred width according to the content,
              because it would be too expensive. Calculating the preferred
              width can be done by calculating the longest line, but this would
              require applying all the processors to each line. This is
              unfeasible for a larger document, and doing it for small
              documents only would result in inconsistent behaviour.
        """
        return None

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        # Calculate the content height, if it was drawn on a screen with the
        # given width.
        height = 0
        content = self.create_content(width, None)

        # When line wrapping is off, the height should be equal to the amount
        # of lines.
        if not wrap_lines:
            return content.line_count

        # When the number of lines exceeds the max_available_height, just
        # return max_available_height. No need to calculate anything.
        if content.line_count >= max_available_height:
            return max_available_height

        for i in range(content.line_count):
            height += content.get_height_for_line(i, width, get_line_prefix)

            if height >= max_available_height:
                return max_available_height

        return height

    def _get_formatted_text_for_line_func(self, document):
        """
        Create a function that returns the fragments for a given line.
        """
        # Cache using `document.text`.
        def get_formatted_text_for_line():
            return self.lexer.lex_document(document)

        key = (document.text, self.lexer.invalidation_hash())
        return self._fragment_cache.get(key, get_formatted_text_for_line)

    def _create_get_processed_line_func(self, document, width, height):
        """
        Create a function that takes a line number of the current document and
        returns a _ProcessedLine(processed_fragments, source_to_display, display_to_source)
        tuple.
        """
        # Merge all input processors together.
        input_processors = self.input_processors or []
        if self.include_default_input_processors:
            input_processors = self.default_input_processors + input_processors

        merged_processor = merge_processors(input_processors)

        def transform(lineno, fragments):
            " Transform the fragments for a given line number. "
            # Get cursor position at this line.
            if document.cursor_position_row == lineno:
                cursor_column = document.cursor_position_col
            else:
                cursor_column = None

            def source_to_display(i):
                """ X position from the buffer to the x position in the
                processed fragment list. By default, we start from the 'identity'
                operation. """
                return i

            transformation = merged_processor.apply_transformation(
                TransformationInput(
                    self, document, lineno, source_to_display, fragments,
                    width, height))

            if cursor_column:
                cursor_column = transformation.source_to_display(cursor_column)

            return _ProcessedLine(
                transformation.fragments,
                transformation.source_to_display,
                transformation.display_to_source)

        def create_func():
            get_line = self._get_formatted_text_for_line_func(document)
            cache = {}

            def get_processed_line(i):
                try:
                    return cache[i]
                except KeyError:
                    processed_line = transform(i, get_line(i))
                    cache[i] = processed_line
                    return processed_line
            return get_processed_line

        return create_func()

    def create_content(self, width, height, preview_search=False):
        """
        Create a UIContent.
        """
        buffer = self.buffer

        # Get the document to be shown. If we are currently searching (the
        # search buffer has focus, and the preview_search filter is enabled),
        # then use the search document, which has possibly a different
        # text/cursor position.)
        search_control = self.search_buffer_control
        preview_now = preview_search or bool(
            # Only if this feature is enabled.
            self.preview_search() and

            # And something was typed in the associated search field.
            search_control and search_control.buffer.text and

            # And we are searching in this control. (Many controls can point to
            # the same search field, like in Pyvim.)
            get_app().layout.search_target_buffer_control == self)

        if preview_now:
            ss = self.search_state

            document = buffer.document_for_search(SearchState(
                text=search_control.buffer.text,
                direction=ss.direction,
                ignore_case=ss.ignore_case))
        else:
            document = buffer.document

        get_processed_line = self._create_get_processed_line_func(
            document, width, height)
        self._last_get_processed_line = get_processed_line

        def translate_rowcol(row, col):
            " Return the content column for this coordinate. "
            return Point(x=get_processed_line(row).source_to_display(col), y=row)

        def get_line(i):
            " Return the fragments for a given line number. "
            fragments = get_processed_line(i).fragments

            # Add a space at the end, because that is a possible cursor
            # position. (When inserting after the input.) We should do this on
            # all the lines, not just the line containing the cursor. (Because
            # otherwise, line wrapping/scrolling could change when moving the
            # cursor around.)
            fragments = fragments + [('', ' ')]
            return fragments

        content = UIContent(
            get_line=get_line,
            line_count=document.line_count,
            cursor_position=translate_rowcol(document.cursor_position_row,
                                             document.cursor_position_col))

        # If there is an auto completion going on, use that start point for a
        # pop-up menu position. (But only when this buffer has the focus --
        # there is only one place for a menu, determined by the focused buffer.)
        if get_app().layout.current_control == self:
            menu_position = self.menu_position() if self.menu_position else None
            if menu_position is not None:
                assert isinstance(menu_position, int)
                menu_row, menu_col = buffer.document.translate_index_to_position(menu_position)
                content.menu_position = translate_rowcol(menu_row, menu_col)
            elif buffer.complete_state:
                # Position for completion menu.
                # Note: We use 'min', because the original cursor position could be
                #       behind the input string when the actual completion is for
                #       some reason shorter than the text we had before. (A completion
                #       can change and shorten the input.)
                menu_row, menu_col = buffer.document.translate_index_to_position(
                    min(buffer.cursor_position,
                        buffer.complete_state.original_document.cursor_position))
                content.menu_position = translate_rowcol(menu_row, menu_col)
            else:
                content.menu_position = None

        return content

    def mouse_handler(self, mouse_event):
        """
        Mouse handler for this control.
        """
        buffer = self.buffer
        position = mouse_event.position

        # Focus buffer when clicked.
        if get_app().layout.current_control == self:
            if self._last_get_processed_line:
                processed_line = self._last_get_processed_line(position.y)

                # Translate coordinates back to the cursor position of the
                # original input.
                xpos = processed_line.display_to_source(position.x)
                index = buffer.document.translate_row_col_to_index(position.y, xpos)

                # Set the cursor position.
                if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                    buffer.exit_selection()
                    buffer.cursor_position = index

                elif mouse_event.event_type == MouseEventType.MOUSE_UP:
                    # When the cursor was moved to another place, select the text.
                    # (The >1 is actually a small but acceptable workaround for
                    # selecting text in Vi navigation mode. In navigation mode,
                    # the cursor can never be after the text, so the cursor
                    # will be repositioned automatically.)
                    if abs(buffer.cursor_position - index) > 1:
                        buffer.start_selection(selection_type=SelectionType.CHARACTERS)
                        buffer.cursor_position = index

                    # Select word around cursor on double click.
                    # Two MOUSE_UP events in a short timespan are considered a double click.
                    double_click = self._last_click_timestamp and time.time() - self._last_click_timestamp < .3
                    self._last_click_timestamp = time.time()

                    if double_click:
                        start, end = buffer.document.find_boundaries_of_current_word()
                        buffer.cursor_position += start
                        buffer.start_selection(selection_type=SelectionType.CHARACTERS)
                        buffer.cursor_position += end - start
                else:
                    # Don't handle scroll events here.
                    return NotImplemented

        # Not focused, but focusing on click events.
        else:
            if self.focus_on_click() and mouse_event.event_type == MouseEventType.MOUSE_UP:
                # Focus happens on mouseup. (If we did this on mousedown, the
                # up event will be received at the point where this widget is
                # focused and be handled anyway.)
                get_app().layout.current_control = self
            else:
                return NotImplemented

    def move_cursor_down(self):
        b = self.buffer
        b.cursor_position += b.document.get_cursor_down_position()

    def move_cursor_up(self):
        b = self.buffer
        b.cursor_position += b.document.get_cursor_up_position()

    def get_key_bindings(self):
        """
        When additional key bindings are given. Return these.
        """
        return self.key_bindings

    def get_invalidate_events(self):
        """
        Return the Window invalidate events.
        """
        # Whenever the buffer changes, the UI has to be updated.
        yield self.buffer.on_text_changed
        yield self.buffer.on_cursor_position_changed

        yield self.buffer.on_completions_changed
        yield self.buffer.on_suggestion_set


class SearchBufferControl(BufferControl):
    """
    :class:`.BufferControl` which is used for searching another
    :class:`.BufferControl`.

    :param ignore_case: Search case insensitive.
    """
    def __init__(self, buffer=None, input_processors=None, lexer=None,
                 focus_on_click=False, key_bindings=None,
                 ignore_case=False):
        super(SearchBufferControl, self).__init__(
                buffer=buffer, input_processors=input_processors, lexer=lexer,
                focus_on_click=focus_on_click, key_bindings=key_bindings)

        # If this BufferControl is used as a search field for one or more other
        # BufferControls, then represents the search state.
        self.searcher_search_state = SearchState(ignore_case=ignore_case)
