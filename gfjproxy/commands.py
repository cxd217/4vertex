"""Commands and processing of messages."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from random import randint

from ._globals import BANNER, BANNER_VERSION, PRESETS
from .feature_guide import FEATURE_GUIDE
from .utils import ResponseHelper

################################################################################


def _stripmultispace(string, *, regex=re.compile(r" +")):
    """Coalesce multiple consecutive spaces into one."""

    return regex.sub(" ", string)


PROXY_TAG_REGEX = re.compile(
    re.escape(ResponseHelper.PROXY_TAG_OPEN)
    + r".*?"
    + re.escape(ResponseHelper.PROXY_TAG_CLOSE),
    re.DOTALL,
)


def _stripproxytext(
    string,
    *,
    regex=PROXY_TAG_REGEX,
):
    """Remove <proxy></proxy> tags and their content."""

    return regex.sub("", string)


def _tokenize(string, *, regex=re.compile(r"/+|\w+|\s+|.")):
    """Split a token into '//' or longer, words, white space and punctuation."""

    return (match.group(0) for match in regex.finditer(string))


################################################################################


@dataclass
class Command:
    """User proxy command."""

    # Command name (lowercase, without any leading //)
    name: str

    # Optional command arguments (a single string, must match command argspec)
    args: str = ""

    # Pointer to command function
    func: Callable | None = field(default=None, repr=False, compare=False, kw_only=True)

    # Prefer to this method instead of directly calling func
    def __call__(self, user, jai_req, response):
        if self.func is None:
            raise RuntimeError(f"Calling {self} without a function pointer")
        return self.func(self.args, user, jai_req, response)


class CommandError(Exception):
    """User error raised by commands."""


class CommandExit(Exception):
    """Exception to exit processing early raised by commands."""


COMMANDS = {}


def command(*, argspec: str = "", **kwargs):
    if argspec:
        regex = re.compile(argspec)

    def outer_wrapper(func):
        cmd_name = func.__name__

        @wraps(func)
        def inner_wrapper(args, user, jai_req, response):
            if argspec and not regex.fullmatch(args):  # pyright: ignore[reportPossiblyUnboundVariable]
                if not args:
                    raise CommandError(
                        f'`//{cmd_name}` requires an argument "`{argspec}`".'
                    )
                raise CommandError(
                    f'`//{cmd_name}` only accepts "`{argspec}`", not "`{args}`".'
                )

            if argspec == r"off|on|this" and (setting := kwargs.get("setting")):
                attr = f"use_{setting}"

                if args == "this":
                    setattr(jai_req, attr, True)
                elif args == "on":
                    setattr(jai_req, attr, True)
                    setattr(user, attr, True)
                else:  # "off"
                    setattr(jai_req, attr, False)
                    setattr(user, attr, False)

            return func(args, user, jai_req, response)

        COMMANDS[cmd_name] = {
            "argcount": 1 if argspec else 0,
            "func": inner_wrapper,
        }

        return inner_wrapper

    return outer_wrapper


################################################################################


@command()
def aboutme(args, user, jai_req, response):
    # U+200B ZERO WIDTH SPACE
    response.add_proxy_message(
        f"Your user ID on this proxy is `{user.xuid!r}`.",
        f"You have used this proxy {user.get_rcounter()} time(s).",
        f"You were {user.last_seen_msg()}.",
        "Your commands are:",
        f"\u200b- //fixturns {'on' if user.use_fixturns else 'off'}",
        f"\u200b- //btrick {'on' if user.use_btrick else 'off'}",
        f"\u200b- //noass {'on' if user.use_noass else 'off'}",
        f"\u200b- //dice_char {'on' if user.use_dice_char else 'off'}",
        f"\u200b- //nobot {'on' if user.use_nobot else 'off'}",
        f"\u200b- //ooctrick {'on' if user.use_ooctrick else 'off'}",
        f"\u200b- //prefill {'on' if user.use_prefill else 'off'}",
        f"\u200b- //prefill_mode {user.prefill_mode}",
        f"\u200b- //search {'on' if user.use_search else 'off'}",
        f"\u200b- //think {'on' if user.use_think else 'off'}",
        f"\u200b- //think_text {user.think_text}",
        *(
            f"\u200b- //advset_{setting} {'on' if user.advsettings.get(setting) else 'off'}"
            + (
                f" (value `{getattr(jai_req, setting)}`)"
                if user.advsettings.get(setting)
                else ""
            )
            for setting in [
                "temperature",
                "frequency_penalty",
                "repetition_penalty",
                "top_k",
                "top_p",
            ]
        ),
        "This message uses the server-side Vertex AI service account.",
    )
    raise CommandExit()


@command()
def banner(args, user, jai_req, response):
    user.do_show_banner(BANNER_VERSION)
    return response.add_proxy_message(BANNER, "***")


@command(argspec=r"[A-Za-z]+")
def preset(args, user, jai_req, response):
    if args not in PRESETS:
        raise CommandError(
            f'"`{args}`" is not a valid preset.'
            + " Available presets: "
            + ", ".join(f"`{key}`" for key in PRESETS)
        )

    jai_req.use_preset = PRESETS[args]

    return response.add_proxy_message(f'Added preset "`{args}`" to this message.')


################################################################################

# To add a new "`off|on|this`" command named xyz do the following:
# - Copy-paste any of the commands in here and write your command text
# - Add a "use_xyz" field to models.JaiRequest
# - Add a "use_xyz" getter/setter to xuiduser.UserSettings
# - Include your command's setting to //aboutme output
# - Include your command's documentation to appropiate //help topics
# - Implement your commands' additional logic inside handlers.handle_chat_message


@command(argspec=r"off|on|this", setting="fixturns")
def fixturns(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Fix requests turns {'enabled' if jai_req.use_fixturns else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"off|on|this", setting="btrick")
def btrick(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Braille Trick {'enabled' if jai_req.use_btrick else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"off|on|this", setting="noass")
def noass(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"NoAss {'enabled' if jai_req.use_noass else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"off|on|this", setting="nobot")
def nobot(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Bot description {'omitted' if jai_req.use_nobot else 'kept'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"off|on|this", setting="ooctrick")
def ooctrick(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"OOC Trick {'enabled' if jai_req.use_ooctrick else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"off|on|this", setting="prefill")
def prefill(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Prefill {'enabled' if jai_req.use_prefill else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"0|1|2|3")
def prefill_mode(args, user, jai_req, response):
    mode = int(args)
    user.prefill_mode = mode
    if jai_req.quiet_commands:
        return response

    if mode == 0:
        mode_name = "classic"
    else:
        mode_names = []
        if mode in (1, 3):
            mode_names.append("interaction config")
        if mode in (2, 3):
            mode_names.append("code starter")
        mode_name = " + ".join(mode_names)

    return response.add_proxy_message(f"Prefill mode set to {args} ({mode_name})")


@command(argspec=r"off|on|this", setting="search")
def search(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Google Search {'enabled' if jai_req.use_search else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"off|on|this", setting="think")
def think(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Thinking {'enabled' if jai_req.use_think else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r"keep|remove")
def think_text(args, user, jai_req, response):
    user.think_text = args
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Thinking text will be {'kept' if args == 'keep' else 'removed'}."
        + (
            " Make sure to have `//think on` otherwise no thinking will show up."
            if args == "keep"
            else ""
        )
    )


################################################################################


@command(argspec=r"off|on|this", setting="dice_char")
def dice_char(args, user, jai_req, response):
    if jai_req.quiet_commands:
        return response
    return response.add_proxy_message(
        f"Character dice {'enabled' if jai_req.use_dice_char else 'disabled'}"
        + (" (for this message only)." if args == "this" else ".")
    )


@command(argspec=r".+")
def dice_roll(args, user, jai_req, response):
    dice = args.replace("p", "+").replace("m", "-")

    match = re.fullmatch(
        r"(\d+)?d(\d+)([+-]\d+)?([a-z])?", dice, re.ASCII | re.IGNORECASE
    )
    if not match:
        return response.add_proxy_message(
            f"Invalid dice syntax `{args}`\nUse the `//help dice` command for more info."
        )

    count = min(max(int(match.group(1) or "1", base=10), 1), 100)
    faces = max(int(match.group(2), base=10), 2)
    extra = int((match.group(3) or "0"), base=10)

    rolls = [randint(1, faces) for _ in range(count)]
    result = sum(rolls) + extra

    extra_str = f" {'-' if extra < 0 else '+'} {abs(extra)}" if extra != 0 else ""

    result_str = f"{dice} roll: {' + '.join(map(str, rolls))}{extra_str}"
    if extra != 0 or len(rolls) > 1:
        result_str += f" = {result}"
    result_str += "."

    jai_req.append_message(
        "user",
        "\n".join(
            [
                "<system>",
                f"  User {result_str}.",
                "</system>",
            ]
        ),
    )

    return response.add_proxy_message(result_str)


################################################################################


def _advset(setting, args, user, jai_req, response):
    if args == "this":
        jai_req.advsettings[setting] = True
    elif args == "on":
        jai_req.advsettings[setting] = True
        user.advsettings[setting] = True
    else:  # "off"
        jai_req.advsettings[setting] = False
        user.advsettings[setting] = False

    if jai_req.quiet_commands:
        return response

    value = getattr(jai_req, setting)

    response.add_proxy_message(
        f"Advanced setting {setting} {'enabled' if args != 'off' else 'disabled'}"
        + (" (for this message only)" if args == "this" else "")
        + f" with value `{value}`."
        + (
            "\nMake sure to set it to nonzero in JanitorAI's Generation Settings."
            if not value
            else ""
        )
    )

    if not value:
        raise CommandExit()
    return response


@command(argspec=r"off|on|this")
def advset_temperature(args, user, jai_req, response):
    return _advset("temperature", args, user, jai_req, response)


@command(argspec=r"off|on|this")
def advset_frequency_penalty(args, user, jai_req, response):
    return _advset("frequency_penalty", args, user, jai_req, response)


@command(argspec=r"off|on|this")
def advset_repetition_penalty(args, user, jai_req, response):
    return _advset("repetition_penalty", args, user, jai_req, response)


@command(argspec=r"off|on|this")
def advset_top_k(args, user, jai_req, response):
    return _advset("top_k", args, user, jai_req, response)


@command(argspec=r"off|on|this")
def advset_top_p(args, user, jai_req, response):
    return _advset("top_p", args, user, jai_req, response)


################################################################################


HELP_COMMANDS = "***\n" + FEATURE_GUIDE


HELP_ADVSETTINGS = """***
# **Advanced Settings**

The proxy supports passing advanced settings to Vertex AI to tweak how the AI writes.

You can configure these in JanitorAI's Generation Settings.
Set valid nonzero numbers in JanitorAI to apply them; penalties can be negative.
Most of these also require a specific command to be enabled in the proxy.

## **Temperature**
`//advset_temperature on|off|this`

Controls how predictable the AI's word choises are.
- Low temperature makes the AI prefer more common or likely words.
- High temperature makes the AI to chose words more freely or randomly.

The command requires a nonzero temperature value in this proxy.

## **Max Tokens**
The proxy always ignores this setting.

Limits how much the AI can write, however it cuts the thinking process too early
and usually leads to error and degraded responses, this it is ignored.

## **Context Size**

Controls how much of the chat history JanitorAI sends to the proxy.
- Low context size makes the AI forgetful and unware of older messages.
- High context size provides the AI with much more of the chat history.

Setting context size too high can cause Tokens Per Minute errors.

## **Top K**
`//advset_top_k on|off|this`
Support depends on the selected Vertex AI model.

Sets an exact limit on how many words the AI can pick from.
- Low top k makes the AI pick from a small list of common words.
- High top k makes the AI pick from a larger list of words, common and uncommon.

Setting top k too hich can make the AI to accidentally use gibberish or random symbols.

## **Top P**
`//advset_top_p on|off|this`

Makes the selection of words the AI can pick from based on how likely they are.
- Low top p makes the AI pick only from the most common words.
- High top p makes the pick from a larger selection of words.

## **Repetition Penalty**
`//advset_repetition_penalty on|off|this`
Support depends on the selected Vertex AI model.

Encourages the AI to use new words.

The proxy always implements this setting as "Presence Penalty" for compatibility.

## **Frequency Penalty**
`//advset_frequency_penalty on|off|this`
Support depends on the selected Vertex AI model.

Prevents the AI from overusing specific words it has already generated.
"""


HELP_DICE = """***
# **Dice Commands**

Language models can't produce random numbers without bias.
The proxy can provide the model with random numbers, allowing for a more authentic experience.
Use the `//dice_roll` command to roll any dice you specify, let the proxy generate the outcome and let the model interpret it for you.

## **Dice Specification**

To make a `//dice_roll` you first need to describe which kind of dice you want to roll.
Examples: use `//dice_roll d6` to roll one six-faced dice, use `//dice_roll 3d20` to roll three twenty-faced dice.
The syntax is `//dice_roll [count]d(faces)[(p|m)(extra)]`. Stuff inside (parens) is mandatory, stuff inside [brackets] is optional.
You must specify how many `faces` the dice you want to roll has. Any number greater than 1 works. Examples: `d6`, `d20`, `d100`.
You can add a `count` to roll the given dice multiple times. Any number greater than 1 works. Examples: `2d10`, `8d15`.
You can add or substract a fixed amount, the `extra`, to the end result. You must specify whether to add (`p` for "plus") or substract (`m` for "minus"). Any number works. Examples: `d20p5`, `d3m2`.
You can use all these features at the same time. Example: `//dice_roll 5d20p10` means "roll a twenty-faced dice five times and then add 10".

## **The `//dice_roll` and `//dice_char` commands**

When you use the `//dice_roll` command, you and the model get to see the result. This command is for your use.
When you enable the `//dice_char` command, the proxy will roll a dice on every message, hidden from you, in behalf of the character you are role-playing with.
The `//dice_char` command is for passively providing randomness to the environment and the character.
You can use the `//dice_roll` command multiple times in a single message for multiple separate dice rolls. The `//dice_char` command only rolls one dice per message.

## **Summary**

- `//dice_roll [count]d(faces)[(p|m)(extra)]`
  Rolls a dice for you.

- `//dice_char on|off|this`
  Rolls a hidden dice for the character on every message.
"""


HELP_VERTEX = """***
# **Google Cloud Vertex AI**

Use one Gemini model: `gemini-2.5-flash`, `google/gemini-2.5-flash`,
or `vertex/gemini-2.5-flash`. Model availability depends on your project and region.

Your JanitorAI API Key is a user identifier/access token for this proxy.
It is never sent to Google. Keep the same value to preserve your command settings.
Ask the proxy owner for the access token if access restrictions are enabled.

The server uses PROJECT_ID, GOOGLE_CREDENTIALS and VERTEX_LOCATION (default: global).
There is no API key rotation or other provider connection in this edition.
"""


HELP = {
    "advsettings": HELP_ADVSETTINGS,
    "commands": HELP_COMMANDS,
    "dice": HELP_DICE,
    "vertex": HELP_VERTEX,
}


@command(argspec=r".+")
def help(args, user, jai_req, response):
    response.add_proxy_message(
        HELP.get(
            args,
            (
                f"There is no help topic `{args}`.\n"
                + "Available topics are: `advsettings`, `commands`, `dice`, `vertex`."
            ),
        )
    )
    raise CommandExit()


################################################################################


def parse_message(message: str) -> tuple[list[Command], str]:
    """Parse an message into a list of commands and the message's content."""

    message = message.strip()

    if "//" not in message:
        # No commands to parse
        return [], _stripmultispace(message)

    commands = []
    content = []

    cmd_argcount = 0
    prev_token = ""
    for token in _tokenize(message):
        if not cmd_argcount:
            if prev_token.startswith("//") and (cmd := COMMANDS.get(token.lower())):
                cmd_argcount = cmd["argcount"]
                commands.append(Command(token, func=cmd["func"]))
                content.pop()  # Remove "//" token
            else:
                content.append(token)
        elif token.isspace():
            continue  # Skip white space between a command and its arguments
        elif token.isalnum():
            cmd_argcount -= 1
            commands[-1].args += token  # Valid argument
        else:
            # Invalid token means there was no valid or not enough arguments
            cmd_argcount = 0  # Stop parsing
            content.append(token)  # Add the token as if there wasn't a command
            # The command function will show the appropriate error to the user

        prev_token = token

    return commands, _stripmultispace("".join(content).strip())


################################################################################


def strip_message(raw_message: str) -> str:
    """Clean up the text of a message, meant for model's output."""

    message = _stripproxytext(raw_message.strip("\n")).split("\n")

    if not message:
        return ""

    result = []

    for line in message:
        index = max(line.find("-"), line.find("*"))
        if index != -1 and line[:index].isspace():
            line = line.rstrip()
            is_list = True
        else:
            line = line.strip()
            is_list = False

        if is_list:
            line = line[:index] + _stripmultispace(line[index:])
        else:
            line = _stripmultispace(line)

        result.append(line)

    return "\n".join(result)


################################################################################
