#!/usr/bin/env python
from __future__ import unicode_literals
from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML


def prompt_continuation(width, line_number, is_soft_wrap):
    """
    The continuation: display line numbers and '->' before soft wraps.

    Notice that we can return any kind of formatted text from here.

    The prompt continuation doesn't have to be the same width as the prompt
    which is displayed before the first line, but in this example we choose to
    align them. The `width` input that we receive here represents the width of
    the prompt.
    """
    if is_soft_wrap:
        return ' ' * (width - 3) + '-> '
    else:
        text = ('- %i - ' % (line_number + 1)).rjust(width)
        return HTML('<strong>%s</strong>') % text


if __name__ == '__main__':
    print('Press [Meta+Enter] or [Esc] followed by [Enter] to accept input.')
    answer = prompt('Multiline input: ', multiline=True,
                    prompt_continuation=prompt_continuation)
    print('You said: %s' % answer)
