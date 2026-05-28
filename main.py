import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
import os
import dotenv

import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()


# ------------ IDs / CONSTANTS ------------
MAIN_GUILD_ID = 1338455645896310784   # main server ID
APPEAL_GUILD_ID = 1497651620681486338 # appeal server ID
APPEAL_CHANNEL_ID = 1497668613283254312  # appeal review channel ID (in appeal server)
STAFF_ROLE_ID = 1497662209285689575     # staff role in appeal server (ping + permissions)

MAIN_SERVER_INVITE = "https://discord.gg/monkemonkemonke"
SERVER_NAME = "Monke Monke Monke League"
APPEAL_LINK = "https://discord.gg/Dn9N2GdGVT"  # for ban DM

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.dm_messages = True
intents.message_content = False

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Shared helpers ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


EST = ZoneInfo("America/New_York")

def format_time(dt):
    dt = dt.astimezone(EST)
    # Windows‑safe format string (no %-m etc.)
    return dt.strftime("%m/%d/%Y %I:%M %p EST")

def parse_duration(text: str) -> Optional[timedelta]:
    """
    Very simple duration parser.
    Examples: "2h", "2 h", "5d", "5 days", "30m", "30 minutes"
    """
    if not text:
        return None
    text = text.strip().lower()

    num = ""
    unit = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch.isalpha():
            unit += ch
        else:
            continue

    if not num:
        return None
    n = int(num)

    if unit in ("d", "day", "days"):
        return timedelta(days=n)
    if unit in ("h", "hr", "hour", "hours"):
        return timedelta(hours=n)
    if unit in ("m", "min", "mins", "minute", "minutes"):
        return timedelta(minutes=n)
    if unit in ("s", "sec", "secs", "second", "seconds"):
        return timedelta(seconds=n)

    return None

def format_remaining(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Expired"

    hours, rem = divmod(total_seconds, 3600)
    minutes, _ = divmod(rem, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)

async def setup_countdown(message: discord.Message, end_time: datetime):
    """
    Edits the DM embed's Duration field every minute until expiration.
    Used for ban DM.
    """
    while True:
        now = now_utc()
        remaining = end_time - now
        if remaining.total_seconds() <= 0:
            try:
                embed = message.embeds[0]
                for i, field in enumerate(embed.fields):
                    if field.name == "Duration":
                        embed.set_field_at(i, name="Duration", value="Expired", inline=False)
                        break
                await message.edit(embed=embed)
            except Exception:
                pass
            break

        remaining_text = format_remaining(remaining)
        try:
            embed = message.embeds[0]
            for i, field in enumerate(embed.fields):
                if field.name == "Duration":
                    embed.set_field_at(i, name="Duration", value=remaining_text, inline=False)
                    break
            await message.edit(embed=embed)
        except Exception:
            break

        await asyncio.sleep(60)


# ============================================================
#                       /submit-report
# ============================================================

class SRActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Warning", value="warning"),
            discord.SelectOption(label="Mute/Timeout", value="mute"),
        ]
        super().__init__(
            placeholder="Choose an action...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="sr_action_select"
        )

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        view = SRMemberSelectView(action)
        await interaction.response.edit_message(
            content=f"Action selected: **{action.capitalize()}**. Now choose a member:",
            view=view
        )

class SRActionSelectView(discord.ui.View):
    def __init__(self, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRActionSelect())

class SRMemberSelect(discord.ui.UserSelect):
    def __init__(self, action: str):
        super().__init__(
            placeholder="Select a member...",
            min_values=1,
            max_values=1,
            custom_id="sr_member_select"
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        member = self.values[0]
        action = self.action
        # Open appropriate modal
        if action in ("ban", "mute"):
            modal = SRDurationReasonModal(action=action, target=member)
        else:
            modal = SRReasonOnlyModal(action=action, target=member)
        await interaction.response.send_modal(modal)

class SRMemberSelectView(discord.ui.View):
    def __init__(self, action: str, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.add_item(SRMemberSelect(action))

class SRDurationReasonModal(discord.ui.Modal, title="Submit Report"):
    def __init__(self, action: str, target: discord.Member | discord.User):
        super().__init__(timeout=300)
        self.action = action
        self.target = target

        self.duration_input = discord.ui.TextInput(
            label="Duration (e.g. 2h, 5 days)",
            placeholder="2h",
            required=True,
            max_length=50
        )
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Because...",
            required=True,
            max_length=400
        )
        self.add_item(self.duration_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        duration_text = self.duration_input.value
        reason = self.reason_input.value
        delta = parse_duration(duration_text)
        if delta is None:
            await interaction.response.send_message(
                "Could not parse that duration. Use things like `2h`, `5 days`, `30m`.",
                ephemeral=True
            )
            return

        now = now_utc()
        end_time = now + delta

        if self.action == "ban":
            # Ban DM
            embed = discord.Embed(
                title="You have been banned.",
                description=f"Appeal: join [this server]({APPEAL_LINK}) and run `/appeal`.",
                color=discord.Color.red()
            )
            embed.add_field(name="Rule", value="Testing server", inline=False)
            embed.add_field(name="Duration", value=format_remaining(delta), inline=False)
            embed.set_footer(text=format_time(now))

            dm_msg = None
            try:
                dm_msg = await self.target.send(embed=embed)
            except Exception:
                pass

            # Perform the ban
            guild = interaction.guild
            try:
                await guild.ban(self.target, reason=reason, delete_message_days=0)
            except Exception as e:
                await interaction.response.send_message(
                    f"Failed to ban user: {e}",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"{self.target.mention} has been **banned** for `{duration_text}`.\nReason: {reason}",
                ephemeral=True
            )

            if dm_msg:
                bot.loop.create_task(setup_countdown(dm_msg, end_time))

        elif self.action == "mute":
            # Mute DM
            embed = discord.Embed(
                title=f"You Have Been Muted In {SERVER_NAME}",
                color=discord.Color.dark_gray()
            )
            embed.add_field(name="Reason:", value=reason, inline=False)
            embed.add_field(name="Duration:", value=duration_text, inline=False)
            embed.set_footer(text=format_time(now))

            try:
                await self.target.send(embed=embed)
            except Exception:
                pass

            # Apply timeout (if member)
            if isinstance(self.target, discord.Member):
                try:
                    await self.target.edit(timeout=end_time)
                except Exception:
                    pass

            await interaction.response.send_message(
                f"{self.target.mention} has been **muted** for `{duration_text}`.\nReason: {reason}",
                ephemeral=True
            )

class SRReasonOnlyModal(discord.ui.Modal, title="Submit Report"):
    def __init__(self, action: str, target: discord.Member | discord.User):
        super().__init__(timeout=300)
        self.action = action
        self.target = target
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Because...",
            required=True,
            max_length=400
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
            await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return

        reason = self.reason_input.value
        now = now_utc()

        if self.action == "warning":
            embed = discord.Embed(
                title=f"You have been warned in {SERVER_NAME}",
                color=discord.Color.yellow()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=format_time(now))

            try:
                await self.target.send(embed=embed)
            except Exception:
                pass

            await interaction.response.send_message(
                f"{self.target.mention} has been **warned**.\nReason: {reason}",
                ephemeral=True
            )


@bot.tree.command(name="submit-report", description="Submit a moderation report (admins only)")
@app_commands.guilds(discord.Object(id=MAIN_GUILD_ID))
async def submit_report(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != MAIN_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the main server.", ephemeral=True)
        return
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You must be an admin to use this command.", ephemeral=True)
        return

    view = SRActionSelectView()
    await interaction.response.send_message(
        "Choose an action for this report:",
        view=view,
        ephemeral=True
    )


# ============================================================
#                           /appeal
# ============================================================

appeal_history: Dict[int, List[datetime]] = {}
active_appeals: Dict[int, int] = {}          # user_id -> thread_id
pending_appeal_queue: List[int] = []         # user ids in queue order

def can_submit_appeal(user_id: int) -> Tuple[bool, Optional[str]]:
    """
    - max 1 appeal every 3 months (approx 90 days)
    - max 6 appeals lifetime (in memory)
    """
    history = appeal_history.get(user_id, [])
    if len(history) >= 6:
        return False, "You have reached the maximum of 6 appeals and cannot appeal anymore."

    if history:
        last = max(history)
        if now_utc() - last < timedelta(days=90):
            return False, "You may only submit one appeal every 3 months. Please try again later."

    return True, None

class AgreementView(discord.ui.View):
    def __init__(self, user_id: int, timeout: Optional[float] = 120):
        super().__init__(timeout=timeout)
        self.user_id = user_id

    @discord.ui.button(label="I Agree", style=discord.ButtonStyle.success, custom_id="appeal_agree")
    async def agree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your agreement.", ephemeral=True)
            return

        ok, msg = can_submit_appeal(interaction.user.id)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        modal = AppealModal()
        await interaction.response.send_modal(modal)

class AppealModal(discord.ui.Modal, title="Ban Appeal Form"):
    date_reason = discord.ui.TextInput(
        label="1. DATE of ban and reason",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )
    explanation = discord.ui.TextInput(
        label="2. Explanation of incident",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    reason_for_appeal = discord.ui.TextInput(
        label="3. Reason for appeal / changes since ban",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    commitments = discord.ui.TextInput(
        label="4. Commitments to future behavior",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )
    comments = discord.ui.TextInput(
        label="5. Any additional comments",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.guild.id != APPEAL_GUILD_ID:
            await interaction.response.send_message("This command can only be used in the appeal server.", ephemeral=True)
            return

        user = interaction.user
        # record
        history = appeal_history.setdefault(user.id, [])
        history.append(now_utc())

        # queue
        if user.id not in pending_appeal_queue:
            pending_appeal_queue.append(user.id)
        position = pending_appeal_queue.index(user.id) + 1

        created_at = now_utc()

        embed = discord.Embed(
            title=f"{user} (@{user.name}) has submitted a ban appeal.",
            color=discord.Color.orange()
        )
        embed.add_field(name="User", value=f"{user.mention}", inline=False)
        embed.add_field(name="User ID", value=str(user.id), inline=False)
        embed.add_field(name="1. DATE of ban and reason", value=str(self.date_reason), inline=False)
        embed.add_field(name="2. Explanation of incident", value=str(self.explanation), inline=False)
        embed.add_field(name="3. Reason for appeal / changes since ban", value=str(self.reason_for_appeal), inline=False)
        embed.add_field(name="4. Commitments to future behavior", value=str(self.commitments), inline=False)
        embed.add_field(name="5. Any additional comments", value=str(self.comments) if self.comments else "None", inline=False)
        embed.set_footer(text=format_time(created_at))

        channel = interaction.client.get_channel(APPEAL_CHANNEL_ID)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message("Appeal channel not found or misconfigured.", ephemeral=True)
            return

        view = StaffDecisionView(target_user_id=user.id)
        staff_mention = f"<@&{STAFF_ROLE_ID}>" if STAFF_ROLE_ID else ""
        appeal_msg = await channel.send(content=staff_mention, embed=embed, view=view)

        thread = await appeal_msg.create_thread(
            name=f"Appeal - {user.name} ({user.id})",
            auto_archive_duration=1440
        )
        active_appeals[user.id] = thread.id

        try:
            dm_embed = discord.Embed(
                title="Appeal Started!",
                description=(
                    "Your appeal has been started! Any messages you send here will be sent to the appeal team.\n\n"
                    "Feel free to add any relevant information about your situation."
                ),
                color=discord.Color.orange()
            )
            await user.send(embed=dm_embed)
        except Exception:
            pass

        await interaction.response.send_message(
            f"Your appeal has been submitted to the appeal team.\n"
            f"You are currently **position {position}** in the appeal queue.",
            ephemeral=True
        )

class StaffDecisionView(discord.ui.View):
    def __init__(self, target_user_id: int, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.target_user_id = target_user_id

    async def _check_staff(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("Not in a guild.", ephemeral=True)
            return False
        if STAFF_ROLE_ID is None:
            return True
        role = interaction.guild.get_role(STAFF_ROLE_ID)
        if role not in interaction.user.roles:
            await interaction.response.send_message("You do not have permission to handle appeals.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="appeal_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_staff(interaction):
            return

        user_id = self.target_user_id
        client = interaction.client

        # Try to fetch the user object (not just from cache)
        try:
            user = await client.fetch_user(user_id)
        except Exception:
            user = client.get_user(user_id)

        # DM user with new message text
        if user is not None:
            try:
                msg = (
                    "You have Been Unbanned join our server here:\n"
                    "[Main Server](https://discord.gg/monkemonkemonke)"
                )
                await user.send(msg)
            except Exception:
                pass

        # Unban in main guild – use user ID directly
        main_guild = client.get_guild(MAIN_GUILD_ID)
        if main_guild:
            try:
                # This works even if the user is not cached
                await main_guild.unban(discord.Object(id=user_id), reason="Appeal accepted")
            except discord.NotFound:
                # Not currently banned – ignore
                pass
            except Exception:
                # You can log this if you want
                pass

        # Kick from appeal server
        appeal_guild = client.get_guild(APPEAL_GUILD_ID)
        if appeal_guild:
            try:
                member = appeal_guild.get_member(user_id)
                if member:
                    await member.kick(reason="Appeal accepted - removed from appeal server")
            except Exception:
                pass

        # Remove from queues
        if user_id in pending_appeal_queue:
            pending_appeal_queue.remove(user_id)
        active_appeals.pop(user_id, None)

        # Disable buttons
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(content="Appeal **ACCEPTED**.", view=self)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="appeal_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_staff(interaction):
            return

        user_id = self.target_user_id
        user = interaction.client.get_user(user_id)
        if user is not None:
            try:
                await user.send("Your appeal has been Denied")
            except Exception:
                pass

        if user_id in pending_appeal_queue:
            pending_appeal_queue.remove(user_id)
        active_appeals.pop(user_id, None)

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

        await interaction.response.edit_message(content="Appeal **DENIED**.", view=self)

@bot.tree.command(name="appeal", description="Submit a ban appeal")
@app_commands.guilds(discord.Object(id=APPEAL_GUILD_ID))
async def appeal(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != APPEAL_GUILD_ID:
        await interaction.response.send_message("This command can only be used in the appeal server.", ephemeral=True)
        return

    ok, msg = can_submit_appeal(interaction.user.id)
    if not ok:
        await interaction.response.send_message(msg, ephemeral=True)
        return

    agreement_text = (
        "**Ban Appeal Agreement**\n"
        "By submitting this ban appeal, you agree to the following terms:\n\n"
        "• Only one unban request every 3 months.\n"
        "• There is a maximum appeal of 6 – if you are not accepted by the 6th appeal, you cannot appeal anymore.\n"
        "• Honesty is required. Dishonesty = immediate voiding of the appeal.\n"
        "• Submitting an appeal does not guarantee an unban.\n\n"
        "Click the button below to proceed."
    )

    view = AgreementView(user_id=interaction.user.id)
    await interaction.response.send_message(agreement_text, view=view, ephemeral=True)

# ---------- Relay DM messages to appeal thread ----------

@bot.event
async def on_message(message: discord.Message):
    # Let commands still work
    await bot.process_commands(message)

    if message.author.bot:
        return

    # Only handle DMs from users with active appeals
    if message.guild is not None:
        return

    user_id = message.author.id
    if user_id not in active_appeals:
        return

    thread_id = active_appeals[user_id]
    thread = bot.get_channel(thread_id)
    if not isinstance(thread, discord.Thread):
        return

    content = message.content or "[no text]"
    attachments = message.attachments

    text = f"**Message from {message.author} ({message.author.id}) in DM:**\n{content}"

    files = []
    for att in attachments:
        try:
            files.append(await att.to_file())
        except Exception:
            pass

    await thread.send(content=text, files=files)

# ---------- on_ready / sync ----------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")

    # Sync main guild commands (for /submit-report)
    main_guild = discord.Object(id=MAIN_GUILD_ID)
    bot.tree.copy_global_to(guild=main_guild)
    await bot.tree.sync(guild=main_guild)

    # Sync appeal guild commands (for /appeal)
    appeal_guild = discord.Object(id=APPEAL_GUILD_ID)
    bot.tree.copy_global_to(guild=appeal_guild)
    await bot.tree.sync(guild=appeal_guild)

    print("Slash commands synced for main and appeal guilds.")

bot.run(os.getenv("TOKEN"))
