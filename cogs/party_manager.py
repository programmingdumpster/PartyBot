# party_bot/cogs/party_manager.py

import disnake
from disnake.ext import commands, tasks
import asyncio
import datetime
# import uuid # Ten import wydaje się nieużywany w tym pliku
import json
import os

# Importy z tego samego pakietu (cogs) i katalogu nadrzędnego (dla config)
import config  # Zakłada, że config.py jest w Party_bot/
from cogs import party_creation_flow
from cogs.party_join_logic import JoinRequestApprovalView
from cogs.party_leader_actions import LeaderControlPanelView

# --- Globalny Stan dla tego Modułu (dostępny dla funkcji w tym pliku i dla Coga) ---
active_parties = {}
parties_awaiting_extension_reply = {}


# --- Funkcje Persystencji ---
def _ensure_data_dir_exists():
    if not os.path.exists(config.DATA_DIR):
        try:
            os.makedirs(config.DATA_DIR)
            print(f"INFO: Utworzono katalog danych: {config.DATA_DIR}")
        except OSError as e:
            print(f"BŁĄD KRYTYCZNY: Nie można utworzyć katalogu danych {config.DATA_DIR}: {e}")


def save_party_data():
    _ensure_data_dir_exists()
    try:
        data_to_save = {}
        for party_id, party_data_instance in active_parties.items():
            data_to_save[party_id] = {
                "emblem_message_id": party_data_instance.get("emblem_message_id"),
                "guild_id": party_data_instance.get("guild_id"),
                "leader_id": party_data_instance.get("leader_id"),
                "party_name": party_data_instance.get("party_name"),
                "game_name": party_data_instance.get("game_name"),
                "category_id": party_data_instance.get("category_id"),
                "settings_channel_id": party_data_instance.get("settings_channel_id"),
                "settings_embed_message_id": party_data_instance.get("settings_embed_message_id"),
                "text_channel_id": party_data_instance.get("text_channel_id"),
                "voice_channel_id": party_data_instance.get("voice_channel_id"),
                "voice_channel_id_2": party_data_instance.get("voice_channel_id_2"),
                "member_ids": party_data_instance.get("member_ids", []),
                "pending_join_requests": party_data_instance.get("pending_join_requests", []),
                "expiry_timestamp": party_data_instance.get("expiry_timestamp"),
                "next_reminder_timestamp": party_data_instance.get("next_reminder_timestamp"),
                "reminder_sent_for_current_cycle": party_data_instance.get("reminder_sent_for_current_cycle", False),
                "leader_panel_dm_id": party_data_instance.get("leader_panel_dm_id"),
                "extension_reminder_dm_id": party_data_instance.get("extension_reminder_dm_id")
            }
        with open(config.PARTY_DATA_FILE, 'w') as f:
            json.dump(data_to_save, f, indent=4)
    except IOError as e:
        print(f"BŁĄD: Nie udało się zapisać danych party do {config.PARTY_DATA_FILE}: {e}")
    except TypeError as e:
        print(f"BŁĄD: Problem z serializacją danych party (TypeError): {e}")
    except Exception as e:
        print(f"BŁĄD KRYTYCZNY: Nieoczekiwany błąd podczas zapisywania danych party: {e}")


def load_party_data():
    global active_parties
    _ensure_data_dir_exists()
    if os.path.exists(config.PARTY_DATA_FILE):
        try:
            with open(config.PARTY_DATA_FILE, 'r') as f:
                loaded_data = json.load(f)
                active_parties = {int(k): v for k, v in loaded_data.items()}
                print(f"INFO: Dane party załadowane z {config.PARTY_DATA_FILE}. Liczba party: {len(active_parties)}")
                for party_id, party_data_instance in list(active_parties.items()):
                    party_data_instance["reminder_sent_for_current_cycle"] = False
                    if party_id in parties_awaiting_extension_reply:
                        del parties_awaiting_extension_reply[party_id]
        except (IOError, json.JSONDecodeError) as e:
            print(
                f"BŁĄD: Nie udało się załadować danych party z {config.PARTY_DATA_FILE}: {e}. Rozpoczynam z pustym stanem.")
            active_parties = {}
        except Exception as e:
            print(
                f"BŁĄD KRYTYCZNY: Nieoczekiwany błąd podczas ładowania danych party: {e}. Rozpoczynam z pustym stanem.")
            active_parties = {}
    else:
        print(f"INFO: Plik danych {config.PARTY_DATA_FILE} nie istnieje. Rozpoczynam z pustym stanem.")
        active_parties = {}


# NOWY WIDOK DLA PRZYCISKU TWORZENIA PARTY
class CreatePartyButtonView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Widok persystentny
        self.add_item(disnake.ui.Button(
            label="🎉 Stwórz Party",
            style=disnake.ButtonStyle.success,
            custom_id="trigger_party_command"
        ))


class PartySettingsView(disnake.ui.View):
    def __init__(self, party_id: int):
        super().__init__(timeout=None)
        self.add_item(disnake.ui.Button(label="Poproś o Dołączenie", style=disnake.ButtonStyle.success,
                                        custom_id=f"settings_request_join_{party_id}"))
        self.add_item(disnake.ui.Button(label="Opuść Party", style=disnake.ButtonStyle.danger,
                                        custom_id=f"settings_leave_party_{party_id}"))


class PartyManagementCog(commands.Cog, name="Zarządzanie Party"):
    _create_party_message_id: int | None = None  # ID wiadomości z przyciskiem Stwórz Party

    def __init__(self, bot_instance: commands.Bot):
        self.bot = bot_instance
        load_party_data()
        self.extension_check_loop.start()
        # Wczytaj ID wiadomości, jeśli było zapisane
        self._load_create_party_message_id()
        print("Cog 'Zarządzanie Party' został załadowany.")

    def cog_unload(self):
        self.extension_check_loop.cancel()
        save_party_data()
        # Zapisz ID wiadomości
        self._save_create_party_message_id()
        print("Cog 'Zarządzanie Party' został odładowany, dane zapisane.")

    def _get_create_party_message_id_path(self):
        _ensure_data_dir_exists()
        return os.path.join(config.DATA_DIR, "create_party_message_id.json")

    def _load_create_party_message_id(self):
        path = self._get_create_party_message_id_path()
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                    PartyManagementCog._create_party_message_id = data.get("message_id")
                    print(f"INFO: Załadowano ID wiadomości Stwórz Party: {PartyManagementCog._create_party_message_id}")
            except (IOError, json.JSONDecodeError) as e:
                print(f"BŁĄD: Nie udało się załadować ID wiadomości Stwórz Party: {e}")
                PartyManagementCog._create_party_message_id = None

    def _save_create_party_message_id(self):
        path = self._get_create_party_message_id_path()
        try:
            with open(path, 'w') as f:
                json.dump({"message_id": PartyManagementCog._create_party_message_id}, f)
                # print(f"INFO: Zapisano ID wiadomości Stwórz Party: {PartyManagementCog._create_party_message_id}")
        except IOError as e:
            print(f"BŁĄD: Nie udało się zapisać ID wiadomości Stwórz Party: {e}")

    async def _send_or_update_stworz_party_message(self, channel: disnake.TextChannel):
        """Wysyła lub aktualizuje wiadomość z przyciskiem na kanale stworz-party."""
        embed = disnake.Embed(
            title="🎉 Stwórz Nowe Party!",
            description="Kliknij poniższy przycisk, aby rozpocząć proces tworzenia party.\nZostaniesz poprowadzony przez kolejne kroki w wiadomościach prywatnych (DM).",
            color=disnake.Color.green()
        )
        if hasattr(config, 'PARTY_EMBED_IMAGE_URL') and config.PARTY_EMBED_IMAGE_URL:
            embed.set_image(url=config.PARTY_EMBED_IMAGE_URL)
        view = CreatePartyButtonView()

        # Spróbuj edytować istniejącą wiadomość, jeśli znamy jej ID
        if PartyManagementCog._create_party_message_id:
            try:
                msg = await channel.fetch_message(PartyManagementCog._create_party_message_id)
                await msg.edit(embed=embed, view=view)
                # print(f"INFO: Zaktualizowano wiadomość 'Stwórz Party' na kanale {channel.name}.")
                return msg
            except disnake.NotFound:
                PartyManagementCog._create_party_message_id = None
                self._save_create_party_message_id()  # Zapisz None
                print(
                    f"INFO: Poprzednia wiadomość 'Stwórz Party' (ID: {PartyManagementCog._create_party_message_id}) nie znaleziona. Tworzę nową.")
            except disnake.HTTPException as e:
                print(
                    f"BŁĄD: Nie udało się edytować wiadomości 'Stwórz Party' (ID: {PartyManagementCog._create_party_message_id}): {e}")
                # Spróbuj wysłać nową poniżej, ale nie resetuj ID od razu, może być chwilowy problem

        # Jeśli nie ma ID lub edycja się nie powiodła, wyślij nową wiadomość
        # Najpierw można opcjonalnie usunąć stare wiadomości bota z tego kanału
        try:
            # Usuń poprzednie wiadomości bota (inne niż ta właściwa, jeśli istnieje), aby zachować czystość
            async for message in channel.history(limit=20):  # Przeszukaj ostatnie wiadomości
                if message.author == self.bot.user:
                    # Jeśli to jest nasza wiadomość (np. ID się zgubiło, ale odnaleźliśmy ją)
                    if message.components and len(message.components) > 0 and \
                            isinstance(message.components[0], disnake.ActionRow) and \
                            len(message.components[0].children) > 0 and \
                            hasattr(message.components[0].children[0], 'custom_id') and \
                            message.components[0].children[0].custom_id == "trigger_party_command":

                        if PartyManagementCog._create_party_message_id is None or PartyManagementCog._create_party_message_id != message.id:
                            PartyManagementCog._create_party_message_id = message.id
                            self._save_create_party_message_id()
                            try:
                                await message.edit(embed=embed, view=view)
                                print(
                                    f"INFO: Odnaleziono i zaktualizowano istniejącą wiadomość 'Stwórz Party' na kanale {channel.name}. ID: {message.id}")
                                return message  # Znaleziono i zaktualizowano
                            except Exception as e_edit:
                                print(f"BŁĄD: Nie udało się edytować odnalezionej wiadomości 'Stwórz Party': {e_edit}")
                                # Nie usuwaj jej od razu, może być problem z edycją, ale wiadomość jest poprawna
                        elif PartyManagementCog._create_party_message_id == message.id:
                            # To jest nasza wiadomość, którą już próbowaliśmy edytować i się nie udało
                            # lub którą właśnie edytowaliśmy pomyślnie (jeśli return msg zadziałało wyżej)
                            # Nie rób nic więcej z tą konkretną wiadomością w tej pętli
                            continue
                    elif message.id != PartyManagementCog._create_party_message_id:  # Inna wiadomość bota
                        try:
                            await message.delete()
                            # print(f"INFO: Usunięto starą wiadomość bota ({message.id}) z kanału {channel.name}")
                        except disnake.HTTPException:
                            pass  # Ignoruj błędy przy usuwaniu starych

            new_msg = await channel.send(embed=embed, view=view)
            PartyManagementCog._create_party_message_id = new_msg.id
            self._save_create_party_message_id()
            print(f"INFO: Wysłano nową wiadomość 'Stwórz Party' na kanale {channel.name}. ID: {new_msg.id}")
            return new_msg
        except disnake.Forbidden:
            print(
                f"BŁĄD KRYTYCZNY: Bot nie ma uprawnień do wysyłania/zarządzania wiadomościami na kanale {channel.name}.")
        except disnake.HTTPException as e:
            print(f"BŁĄD KRYTYCZNY: Nie udało się wysłać wiadomości 'Stwórz Party' na kanał {channel.name}: {e}")
        return None

    @commands.Cog.listener("on_ready")
    async def on_ready_setup_stworz_party_channel(self):
        print("PartyManagementCog: Bot jest gotowy. Ustawianie kanału 'stworz-party'...")
        await asyncio.sleep(5)  # Daj botowi chwilę na pełne załadowanie gildii, zwłaszcza przy większej liczbie

        for guild in self.bot.guilds:
            stworz_party_channel = disnake.utils.get(guild.text_channels, name=config.STWORZ_PARTY_CHANNEL_NAME)
            if stworz_party_channel:
                await self._send_or_update_stworz_party_message(stworz_party_channel)
                try:
                    current_perms_everyone = stworz_party_channel.overwrites_for(guild.default_role)
                    if current_perms_everyone.send_messages is not False or current_perms_everyone.create_public_threads is not False or current_perms_everyone.create_private_threads is not False:
                        new_overwrite = disnake.PermissionOverwrite()
                        new_overwrite.send_messages = False
                        new_overwrite.create_public_threads = False  # Dodatkowo blokujemy wątki
                        new_overwrite.create_private_threads = False
                        await stworz_party_channel.set_permissions(guild.default_role, overwrite=new_overwrite,
                                                                   reason="Automatyczna konfiguracja kanału tworzenia party.")
                        print(
                            f"INFO: Ustawiono blokadę pisania i tworzenia wątków dla @everyone na '{config.STWORZ_PARTY_CHANNEL_NAME}' w {guild.name}.")

                    current_perms_bot = stworz_party_channel.overwrites_for(guild.me)
                    if current_perms_bot.send_messages is not True or \
                            current_perms_bot.embed_links is not True or \
                            current_perms_bot.manage_messages is not True or \
                            current_perms_bot.read_message_history is not True:  # Ważne dla czyszczenia
                        bot_overwrite = disnake.PermissionOverwrite()
                        bot_overwrite.send_messages = True
                        bot_overwrite.embed_links = True
                        bot_overwrite.manage_messages = True
                        bot_overwrite.read_message_history = True
                        await stworz_party_channel.set_permissions(guild.me, overwrite=bot_overwrite,
                                                                   reason="Automatyczne uprawnienia dla bota na kanale tworzenia party.")
                        print(
                            f"INFO: Upewniono się, że bot ma uprawnienia na '{config.STWORZ_PARTY_CHANNEL_NAME}' w {guild.name}.")
                except disnake.Forbidden:
                    print(
                        f"BŁĄD: Bot nie ma uprawnień 'Zarządzanie Kanałem'/'Zarządzanie Uprawnieniami' na '{config.STWORZ_PARTY_CHANNEL_NAME}' w {guild.name}.")
                except Exception as e:
                    print(
                        f"BŁĄD podczas ustawiania uprawnień kanału '{config.STWORZ_PARTY_CHANNEL_NAME}' w {guild.name}: {e}")
            else:
                print(
                    f"WARN: Kanał '{config.STWORZ_PARTY_CHANNEL_NAME}' nie znaleziony w gildii {guild.name} podczas on_ready.")

    @commands.slash_command(
        name="setupstworzparty",
        description="Konfiguruje kanał 'stworz-party' z embedem i przyciskiem (admin)."
    )
    @commands.has_permissions(administrator=True)
    async def setup_stworz_party_slash(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        if not inter.guild:
            await inter.followup.send("Ta komenda musi być użyta na serwerze.", ephemeral=True)
            return

        stworz_party_channel = disnake.utils.get(inter.guild.text_channels, name=config.STWORZ_PARTY_CHANNEL_NAME)
        if not stworz_party_channel:
            await inter.followup.send(
                f"Kanał `{config.STWORZ_PARTY_CHANNEL_NAME}` nie został znaleziony. Utwórz go najpierw.",
                ephemeral=True)
            return

        msg_sent = await self._send_or_update_stworz_party_message(stworz_party_channel)
        perm_message = ""
        try:
            current_perms_everyone = stworz_party_channel.overwrites_for(inter.guild.default_role)
            if current_perms_everyone.send_messages is not False or current_perms_everyone.create_public_threads is not False or current_perms_everyone.create_private_threads is not False:
                new_overwrite = disnake.PermissionOverwrite()
                new_overwrite.send_messages = False
                new_overwrite.create_public_threads = False
                new_overwrite.create_private_threads = False
                await stworz_party_channel.set_permissions(inter.guild.default_role, overwrite=new_overwrite,
                                                           reason="Ręczna konfiguracja kanału tworzenia party.")
            current_perms_bot = stworz_party_channel.overwrites_for(inter.guild.me)
            if current_perms_bot.send_messages is not True or current_perms_bot.embed_links is not True or current_perms_bot.manage_messages is not True or current_perms_bot.read_message_history is not True:
                bot_overwrite = disnake.PermissionOverwrite()
                bot_overwrite.send_messages = True
                bot_overwrite.embed_links = True
                bot_overwrite.manage_messages = True
                bot_overwrite.read_message_history = True
                await stworz_party_channel.set_permissions(inter.guild.me, overwrite=bot_overwrite,
                                                           reason="Ręczna konfiguracja kanału tworzenia party.")
            perm_message = " Uprawnienia zostały sprawdzone/ustawione."
        except disnake.Forbidden:
            perm_message = " Nie udało się ustawić uprawnień (brak permisji bota)."
        except Exception as e:
            perm_message = f" Wystąpił błąd podczas ustawiania uprawnień: {e}"

        if msg_sent:
            await inter.followup.send(
                f"Kanał `{config.STWORZ_PARTY_CHANNEL_NAME}` został skonfigurowany/zaktualizowany.{perm_message}",
                ephemeral=True)
        else:
            await inter.followup.send(
                f"Nie udało się skonfigurować kanału `{config.STWORZ_PARTY_CHANNEL_NAME}`.{perm_message}",
                ephemeral=True)

    async def _update_settings_embed(self, party_id: int):
        party_data = active_parties.get(party_id)
        if not party_data or not party_data.get("settings_channel_id"):
            return

        guild = self.bot.get_guild(party_data["guild_id"])
        if not guild:
            return

        settings_channel = guild.get_channel(party_data["settings_channel_id"])
        if not settings_channel or not isinstance(settings_channel, disnake.TextChannel):
            return

        leader = guild.get_member(party_data["leader_id"])
        members_mentions = [guild.get_member(mid).mention if guild.get_member(mid) else f"ID:{mid}" for mid in
                            party_data.get("member_ids", [])]

        embed_title = f"⚙️ Informacje o Party: {party_data['party_name']}"
        embed_color = disnake.Color.dark_grey()

        embed = disnake.Embed(title=embed_title, color=embed_color)
        embed.add_field(name="👑 Lider", value=leader.mention if leader else f"ID: {party_data['leader_id']}",
                        inline=False)
        embed.add_field(name="👥 Aktualni Członkowie",
                        value="\n".join(members_mentions) if members_mentions else "Brak członków.", inline=False)
        embed.add_field(name="🆔 ID Party (Emblematu Głównego)", value=f"`{party_id}`",
                        inline=False)

        view = PartySettingsView(party_id)

        if party_data.get("settings_embed_message_id"):
            try:
                settings_embed_msg = await settings_channel.fetch_message(party_data["settings_embed_message_id"])
                await settings_embed_msg.edit(embed=embed, view=view)
                return
            except disnake.NotFound:
                print(f"INFO: Poprzednia wiadomość embedu ustawień dla party {party_id} nie znaleziona. Tworzę nową.")
                party_data["settings_embed_message_id"] = None
            except disnake.HTTPException as e:
                print(f"BŁĄD: Aktualizacja embedu ustawień dla party {party_id} nie powiodła się (HTTPException): {e}")
                party_data["settings_embed_message_id"] = None
            except Exception as e:
                print(f"BŁĄD: Nieoczekiwany błąd podczas aktualizacji embedu ustawień dla party {party_id}: {e}")
                party_data["settings_embed_message_id"] = None
        try:
            new_settings_embed_msg = await settings_channel.send(embed=embed, view=view)
            party_data["settings_embed_message_id"] = new_settings_embed_msg.id
            save_party_data()  # Zapis danych po aktualizacji ID wiadomości embedu ustawień
        except disnake.Forbidden:
            print(f"BŁĄD: Bot nie ma uprawnień do wysyłania wiadomości na kanale ustawień party {party_id}.")
        except Exception as e:
            print(f"BŁĄD: Wysyłanie nowego embedu ustawień dla party {party_id}: {e}")

    async def _update_party_emblem(self, party_id: int):
        party_data = active_parties.get(party_id)
        if not party_data: return
        guild = self.bot.get_guild(party_data["guild_id"])
        if not guild: return
        szukam_party_channel = disnake.utils.get(guild.text_channels, name=config.SZUKAM_PARTY_CHANNEL_NAME)
        if not szukam_party_channel: return

        try:
            emblem_message = await szukam_party_channel.fetch_message(party_data["emblem_message_id"])
            leader = guild.get_member(party_data["leader_id"])
            members_mentions = [guild.get_member(mid).mention if guild.get_member(mid) else f"ID:{mid}" for mid in
                                party_data.get("member_ids", [])]
            embed = disnake.Embed(title=f"✨ Party: {party_data['party_name']}",
                                  description="Poproś o dołączenie!",
                                  color=disnake.Color.blurple())
            embed.add_field(name="🎮 Gra", value=party_data["game_name"], inline=True)
            embed.add_field(name="👑 Lider", value=leader.mention if leader else f"ID:{party_data['leader_id']}",
                            inline=True)
            embed.add_field(name="👥 Członkowie", value="\n".join(members_mentions) if members_mentions else "Brak",
                            inline=False)
            embed.set_footer(text=f"ID Party: {party_id}")

            view = disnake.ui.View(timeout=None)
            view.add_item(disnake.ui.Button(label="Poproś o Dołączenie", style=disnake.ButtonStyle.primary,
                                            custom_id=f"request_join_party_{party_id}"))
            await emblem_message.edit(embed=embed, view=view)
        except disnake.NotFound:
            print(
                f"INFO: Nie znaleziono emblematu {party_data.get('emblem_message_id')} dla '{party_data.get('party_name')}'. Mógł zostać usunięty.")
        except Exception as e:
            print(f"BŁĄD: Aktualizacja emblematu '{party_data.get('party_name')}': {e}")

    async def send_leader_control_panel(self, leader: disnake.User, party_id: int):
        party_data = active_parties.get(party_id)
        if not party_data: return
        try:
            dm_channel = await leader.create_dm()
            guild = self.bot.get_guild(party_data["guild_id"])
            members_list_str = [
                f"- {guild.get_member(m_id).mention if guild and guild.get_member(m_id) else f'ID:{m_id}'} (`{m_id}`)"
                for m_id in party_data.get("member_ids", [])
            ]

            embed = disnake.Embed(
                title=f"🛠️ Panel Party: {party_data['party_name']}",
                description=f"**Gra:** {party_data['game_name']}\n**Wygasa:** <t:{int(party_data['expiry_timestamp'])}:F> (<t:{int(party_data['expiry_timestamp'])}:R>)",
                color=disnake.Color.gold()
            )
            embed.add_field(name="👥 Aktualni Członkowie:",
                            value="\n".join(members_list_str) if members_list_str else "Brak", inline=False)
            embed.add_field(
                name="Akcje (komendy w tej konwersacji DM):",
                value=(f"- `{config.DEFAULT_COMMAND_PREFIX}usun_czlonka ID_lub_@wzmianka`\n"
                       f"- `{config.DEFAULT_COMMAND_PREFIX}zmien_nazwe_party nowa nazwa`\n"
                       f"- `{config.DEFAULT_COMMAND_PREFIX}lista_czlonkow` (odświeża ten panel)\n"
                       f"- `{config.DEFAULT_COMMAND_PREFIX}opusc ID_party_lub_nazwa_party`\n"  # Zmieniono z opusc_party
                       f"*(Przycisk 'Rozwiąż Party' jest poniżej)*"),
                inline=False
            )
            embed.set_footer(text=f"ID Twojego Party (dla bota): {party_id}")
            view = LeaderControlPanelView(party_id)

            if party_data.get("leader_panel_dm_id"):
                try:
                    old_panel_msg = await dm_channel.fetch_message(party_data["leader_panel_dm_id"])
                    await old_panel_msg.delete()
                except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                    pass  # Ignoruj, jeśli nie można usunąć starego panelu
                party_data["leader_panel_dm_id"] = None  # Zresetuj ID starego panelu

            new_panel_msg = await dm_channel.send(embed=embed, view=view)
            party_data["leader_panel_dm_id"] = new_panel_msg.id
            save_party_data()  # Zapisz ID nowego panelu
        except disnake.Forbidden:
            print(f"DM ERR: Nie można wysłać panelu lidera do {leader.name} ({leader.id}).")
        except Exception as e:
            print(f"ERR: Nieoczekiwany błąd przy wysyłaniu panelu lidera: {e} (Typ: {type(e)})")

    async def disband_party(self, party_id: int, reason: str = "Party rozwiązane."):
        party_data = active_parties.pop(party_id, None)
        if not party_data: return

        if party_id in parties_awaiting_extension_reply:
            del parties_awaiting_extension_reply[party_id]

        guild = self.bot.get_guild(party_data["guild_id"])
        if guild:
            leader_for_panel_dm = self.bot.get_user(party_data["leader_id"])
            if leader_for_panel_dm and party_data.get("leader_panel_dm_id"):
                try:
                    dm_ch = await leader_for_panel_dm.create_dm()
                    msg_to_delete = await dm_ch.fetch_message(party_data["leader_panel_dm_id"])
                    await msg_to_delete.delete()
                except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                    pass  # Ignoruj błędy przy usuwaniu panelu lidera

            if party_data.get("category_id"):
                category = guild.get_channel(party_data["category_id"])
                if category and isinstance(category, disnake.CategoryChannel):
                    for ch_in_cat in list(category.channels):  # Użyj list() do skopiowania, bo kategoria się zmienia
                        try:
                            await ch_in_cat.delete(reason=reason)
                        except disnake.HTTPException:
                            pass
                    try:
                        await category.delete(reason=reason)
                    except disnake.HTTPException:
                        pass
            else:
                channel_keys_to_delete_individually = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                                       "voice_channel_id_2"]
                for ch_key in channel_keys_to_delete_individually:
                    ch_id_to_del = party_data.get(ch_key)
                    if ch_id_to_del:
                        channel_to_delete = guild.get_channel(ch_id_to_del)
                        if channel_to_delete:
                            try:
                                await channel_to_delete.delete(
                                    reason=f"{reason} (kanał poza kategorią lub kategoria nie znaleziona)")
                            except disnake.HTTPException:
                                pass

            if party_data.get("emblem_message_id"):
                szukam_ch = disnake.utils.get(guild.text_channels, name=config.SZUKAM_PARTY_CHANNEL_NAME)
                if szukam_ch:
                    try:
                        msg = await szukam_ch.fetch_message(party_data["emblem_message_id"])
                        await msg.delete()
                    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                        pass
        else:
            print(
                f"WARN: Gildia {party_data['guild_id']} niedostępna przy rozwiązywaniu party {party_id}. Usuwam tylko dane.")

        save_party_data()  # Zapisz dane po usunięciu party z active_parties

        leader = self.bot.get_user(party_data["leader_id"])
        if leader:
            try:
                await leader.send(
                    f"Twoje party '{party_data.get('party_name', 'N/A')}' zostało rozwiązane. Powód: {reason}")
            except disnake.Forbidden:
                pass  # Ignoruj, jeśli DM zablokowane
        print(f"INFO: Party '{party_data.get('party_name', 'N/A')}' (ID: {party_id}) rozwiązane.")

    # --- ISTNIEJĄCA KOMENDA !PARTY ---
    # Pozostaje bez zmian w sygnaturze, ale jej ctx.message.delete()
    # będzie "zneutralizowane" przez monkeypatching przy wywołaniu z przycisku.
    @commands.command(name="party")
    async def party_command_handler(self, ctx: commands.Context):
        if not ctx.guild:
            await ctx.send("Tej komendy można używać tylko na serwerze.", ephemeral=True)
            return

        # Ta walidacja kanału jest ważna
        if ctx.channel.name != config.STWORZ_PARTY_CHANNEL_NAME:
            # Jeśli ctx.message to fake_message z interakcji, to ctx.send i ctx.message.delete()
            # mogą nie działać zgodnie z oczekiwaniami lub rzucić błąd.
            # Jednakże, przycisk będzie tylko na właściwym kanale, więc ta gałąź nie powinna być trafiona.
            # Dla bezpieczeństwa, można dodać warunek, że to nie jest "fałszywa" wiadomość.
            if not hasattr(ctx.message, '_is_fake_for_interaction'):
                try:
                    await ctx.send(f"Tej komendy można używać tylko na kanale `#{config.STWORZ_PARTY_CHANNEL_NAME}`.",
                                   delete_after=10)
                    await ctx.message.delete(delay=10)
                except disnake.HTTPException:
                    pass
            return

        is_already_leader = any(p_data.get("leader_id") == ctx.author.id for p_data in active_parties.values())
        if is_already_leader:
            leader_of_party_name = next((p_data.get("party_name", "...") for p_data in active_parties.values() if
                                         p_data.get("leader_id") == ctx.author.id), "nieznanego party")
            msg_content = f"{ctx.author.mention}, jesteś już liderem party '{leader_of_party_name}'. Możesz prowadzić tylko jedno party."
            # W przypadku wywołania z przycisku, ctx.send wyśle wiadomość na kanał, co jest OK.
            # ctx.message.delete() zostanie zneutralizowane dla przycisku.
            try:
                response_msg = await ctx.send(msg_content, delete_after=15)
                if not hasattr(ctx.message,
                               '_is_fake_for_interaction'):  # Usuń oryginalną komendę, jeśli to nie interakcja
                    await ctx.message.delete()
                # Nie usuwamy response_msg, bo ma delete_after
            except disnake.HTTPException:
                pass
            return

        try:
            dm_ch = await ctx.author.create_dm()
        except disnake.Forbidden:
            # Podobnie, ctx.send pójdzie na kanał.
            try:
                await ctx.send(f"{ctx.author.mention}, nie mogę Ci wysłać DM. Sprawdź ustawienia prywatności.",
                               delete_after=15)
                if not hasattr(ctx.message, '_is_fake_for_interaction'):
                    await ctx.message.delete()
            except disnake.HTTPException:
                pass
            return

        # TO JEST KLUCZOWY MOMENT DLA PRZYCISKU
        # Jeśli ctx.message to nasz "fake_message", jego delete() zostanie zneutralizowane.
        # Jeśli to prawdziwa komenda, zostanie usunięta.
        try:
            await ctx.message.delete()
        except disnake.HTTPException:
            # print(f"DEBUG: Nie udało się usunąć ctx.message (może być fake): {e}")
            pass
        except AttributeError:
            # print(f"DEBUG: ctx.message.delete nie istnieje (prawdopodobnie fake_message po monkeypatchu)")
            pass

        selected_game = await party_creation_flow.handle_game_selection_dm(self.bot, ctx.author, dm_ch)
        if not selected_game: return

        party_name_input = await party_creation_flow.handle_party_name_dm(self.bot, ctx.author, dm_ch)
        if not party_name_input: return

        leader = ctx.author
        guild = ctx.guild  # type: ignore

        szukam_ch = disnake.utils.get(guild.text_channels, name=config.SZUKAM_PARTY_CHANNEL_NAME)
        if not szukam_ch:
            await dm_ch.send(
                f"Krytyczny błąd: Kanał `#{config.SZUKAM_PARTY_CHANNEL_NAME}` nie został znaleziony na serwerze '{guild.name}'.")
            return

        cat_name = f"🎉 {party_name_input} ({leader.display_name})"
        category_overwrites = {
            guild.default_role: disnake.PermissionOverwrite(
                view_channel=True, read_messages=True, send_messages=False, connect=False, speak=False,
                create_public_threads=False, create_private_threads=False, send_messages_in_threads=False
            ),
            guild.me: disnake.PermissionOverwrite(
                view_channel=True, manage_channels=True, manage_permissions=True,
                read_messages=True, send_messages=True, connect=True, speak=True,
                create_public_threads=True, create_private_threads=True,
                send_messages_in_threads=True, manage_threads=True
            ),
            leader: disnake.PermissionOverwrite(
                view_channel=True, read_messages=True, send_messages=True,
                connect=True, speak=True, manage_messages=True,
                mute_members=True, deafen_members=True, move_members=True,
                create_public_threads=True, create_private_threads=True, send_messages_in_threads=True
            )
        }
        category = None;
        settings_ch = None;
        text_ch = None;
        voice_ch1 = None;
        voice_ch2 = None
        try:
            category = await guild.create_category(name=cat_name, overwrites=category_overwrites)

            settings_ch_name = f"📌︱info-{party_name_input[:20]}"
            settings_ch_overwrites = {
                guild.default_role: disnake.PermissionOverwrite(send_messages=False, add_reactions=False,
                                                                create_public_threads=False,
                                                                create_private_threads=False,
                                                                send_messages_in_threads=False),
                guild.me: disnake.PermissionOverwrite(send_messages=True, embed_links=True, manage_messages=True, )
            }
            settings_ch = await category.create_text_channel(name=settings_ch_name, overwrites=settings_ch_overwrites)

            text_ch_name = f"💬︱{party_name_input[:20]}"
            text_ch = await category.create_text_channel(name=text_ch_name)
            await text_ch.send(
                f"Witaj w party **{party_name_input}**! Lider: {leader.mention}. Gra: **{selected_game}**."
            )

            voice_ch1_name = f"🔊︱Głos 1 ({party_name_input[:15]})"
            voice_ch1 = await category.create_voice_channel(name=voice_ch1_name)

            voice_ch2_name = f"🔊︱Głos 2 ({party_name_input[:15]})"
            voice_ch2 = await category.create_voice_channel(name=voice_ch2_name)

        except disnake.HTTPException as e:
            await dm_ch.send(f"Nie udało się stworzyć kanałów: {e}.")
            if category:
                for c_del in list(category.channels):
                    try:
                        await c_del.delete()
                    except disnake.HTTPException:
                        pass
                try:
                    await category.delete()
                except disnake.HTTPException:
                    pass
            return

        emb = disnake.Embed(title=f"✨ Nowe Party: {party_name_input}", description="Poproś o dołączenie!",
                            color=disnake.Color.green())
        emb.add_field(name="🎮 Gra", value=selected_game, inline=True)
        emb.add_field(name="👑 Lider", value=leader.mention, inline=True)
        emb.add_field(name="👥 Członkowie", value=leader.mention, inline=False)
        emb.set_footer(text="ID Party zostanie przypisane po wysłaniu.")

        pub_join_view = disnake.ui.View(timeout=None)
        pub_join_btn = disnake.ui.Button(label="Poproś o Dołączenie", style=disnake.ButtonStyle.primary,
                                         custom_id=f"request_join_party_TEMP_ID")
        pub_join_view.add_item(pub_join_btn)

        emblem_message = None
        try:
            emblem_message = await szukam_ch.send(embed=emb, view=pub_join_view)
        except disnake.HTTPException as e:
            await dm_ch.send(f"Nie udało się opublikować ogłoszenia: {e}")
            if category:
                for c_del in list(category.channels):
                    try:
                        await c_del.delete()
                    except disnake.HTTPException:
                        pass
                try:
                    await category.delete()
                except disnake.HTTPException:
                    pass
            return

        party_id = emblem_message.id
        pub_join_btn.custom_id = f"request_join_party_{party_id}"
        emb.set_footer(text=f"ID Party: {party_id}")
        try:
            await emblem_message.edit(embed=emb, view=pub_join_view)
        except disnake.HTTPException as e:
            print(f"WARN: Aktualizacja custom_id przycisku dla '{party_name_input}': {e}")

        init_exp_ts = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            hours=config.PARTY_LIFESPAN_HOURS)).timestamp()
        next_rem_ts = init_exp_ts - datetime.timedelta(
            hours=config.EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY).total_seconds()
        if config.PARTY_LIFESPAN_HOURS <= config.EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY:
            next_rem_ts = init_exp_ts

        active_parties[party_id] = {
            "emblem_message_id": party_id, "guild_id": guild.id, "leader_id": leader.id,
            "party_name": party_name_input, "game_name": selected_game,
            "category_id": category.id if category else None,
            "settings_channel_id": settings_ch.id if settings_ch else None,
            "settings_embed_message_id": None,
            "text_channel_id": text_ch.id if text_ch else None,
            "voice_channel_id": voice_ch1.id if voice_ch1 else None,
            "voice_channel_id_2": voice_ch2.id if voice_ch2 else None,
            "member_ids": [leader.id], "pending_join_requests": [],
            "expiry_timestamp": init_exp_ts,
            "next_reminder_timestamp": next_rem_ts,
            "reminder_sent_for_current_cycle": False, "leader_panel_dm_id": None,
            "extension_reminder_dm_id": None
        }

        if settings_ch:
            await self._update_settings_embed(party_id)  # To też robi save_party_data

        # save_party_data() # Jest w _update_settings_embed

        try:
            delete_delay = getattr(config, 'DM_MESSAGE_DELETE_DELAY', 15)
            await dm_ch.send(f"Party '{party_name_input}' stworzone! Panel zarządzania został wysłany.",
                             delete_after=delete_delay)
        except disnake.HTTPException:
            pass

        await self.send_leader_control_panel(leader, party_id)

    @commands.Cog.listener("on_interaction")
    async def on_button_interaction(self, interaction: disnake.MessageInteraction):
        custom_id = interaction.data.get("custom_id")
        if not custom_id: return

        if custom_id == "trigger_party_command":
            if interaction.channel.name != config.STWORZ_PARTY_CHANNEL_NAME:
                await interaction.response.send_message(
                    f"Tego przycisku można używać tylko na kanale `#{config.STWORZ_PARTY_CHANNEL_NAME}`.",
                    ephemeral=True, delete_after=10
                )
                return

            try:
                await interaction.response.defer()
            except disnake.HTTPException:
                print(
                    f"OSTRZEŻENIE: Nie udało się wykonać defer() dla interakcji przycisku 'trigger_party_command' od {interaction.user.name}")
                pass

            party_cmd = self.bot.get_command("party")
            if not party_cmd:
                print("BŁĄD KRYTYCZNY: Komenda 'party' nie została znaleziona w bocie.")
                try:
                    await interaction.followup.send(
                        "Wystąpił wewnętrzny błąd bota (nie znaleziono komendy party). Skontaktuj się z administratorem.",
                        ephemeral=True)
                except disnake.HTTPException:
                    pass
                return

            # --- POPRAWKA TUTAJ ---
            author_data = {
                'id': str(interaction.user.id),
                'username': interaction.user.name,
                'discriminator': interaction.user.discriminator,
                'avatar': interaction.user.avatar.key if interaction.user.avatar else None,
                'bot': interaction.user.bot
            }
            # Jeśli użytkownik ma global_name (nowy system nazw Discorda), można go dodać
            if hasattr(interaction.user, 'global_name'):
                author_data['global_name'] = interaction.user.global_name

            fake_message_data = {
                'id': interaction.id,  # Użyj ID interakcji jako ID "wiadomości"
                'channel_id': interaction.channel_id,
                'guild_id': interaction.guild_id,
                'author': author_data,  # Użyj poprawionego słownika author_data
                'content': f"{config.DEFAULT_COMMAND_PREFIX}party",
                'attachments': [], 'embeds': [], 'edited_timestamp': None, 'type': 0,
                'pinned': False, 'mention_everyone': False, 'tts': False,
                'mentions': [], 'mention_roles': [], 'mention_channels': [],
                'components': [], 'flags': 0,
            }
            # --- KONIEC POPRAWKI ---

            fake_message = disnake.Message(state=interaction._state, channel=interaction.channel,
                                           data=fake_message_data)  # type: ignore
            setattr(fake_message, '_is_fake_for_interaction', True)

            original_delete = fake_message.delete

            async def _dummy_delete_for_interaction(*args, **kwargs):
                pass

            fake_message.delete = _dummy_delete_for_interaction  # type: ignore

            ctx = await self.bot.get_context(fake_message, cls=commands.Context)  # type: ignore

            ctx.author = interaction.user  # type: ignore
            ctx.guild = interaction.guild
            ctx.channel = interaction.channel  # type: ignore
            ctx.command = party_cmd

            try:
                await party_cmd.invoke(ctx)  # type: ignore
            except Exception as e:
                print(f"BŁĄD podczas wywoływania party_command_handler z interakcji (ID: {interaction.id}): {e}")
                import traceback
                traceback.print_exc()
                try:
                    await interaction.followup.send(
                        f"Wystąpił nieoczekiwany błąd podczas próby stworzenia party: {e}. Spróbuj ponownie później.",
                        ephemeral=True)
                except disnake.HTTPException:
                    pass
            return

        elif custom_id.startswith("request_join_party_") or custom_id.startswith("settings_request_join_"):
            await interaction.response.defer(ephemeral=True)
            try:
                if custom_id.startswith("settings_request_join_"):
                    party_id_str = custom_id.replace("settings_request_join_", "")
                else:
                    party_id_str = custom_id.replace("request_join_party_", "")
                party_id = int(party_id_str)
            except (IndexError, ValueError):
                await interaction.followup.send("Błąd wewnętrzny przycisku (ID party).", ephemeral=True);
                return

            user_requesting_join = interaction.user
            party_data = active_parties.get(party_id)
            if not party_data:
                await interaction.followup.send("To party już nie istnieje lub wystąpił błąd.", ephemeral=True);
                return

            if user_requesting_join.id == party_data["leader_id"]:
                await interaction.followup.send("Jesteś liderem tego party, nie musisz prosić o dołączenie.",
                                                ephemeral=True);
                return
            if user_requesting_join.id in party_data["member_ids"]:
                await interaction.followup.send("Już jesteś członkiem tego party!", ephemeral=True);
                return
            if user_requesting_join.id in party_data.get("pending_join_requests", []):
                await interaction.followup.send(
                    "Twoja prośba o dołączenie do tego party już oczekuje na akceptację lidera.", ephemeral=True);
                return

            leader = self.bot.get_user(party_data["leader_id"])
            if not leader:
                try:
                    leader = await self.bot.fetch_user(party_data["leader_id"])
                except disnake.NotFound:
                    await interaction.followup.send("Lider tego party jest obecnie nieosiągalny.", ephemeral=True);
                    return
                except disnake.HTTPException:
                    await interaction.followup.send("Wystąpił błąd sieciowy przy próbie kontaktu z liderem.",
                                                    ephemeral=True);
                    return
            try:
                if user_requesting_join.id not in party_data.get("pending_join_requests", []):
                    party_data.setdefault("pending_join_requests", []).append(user_requesting_join.id)
                    save_party_data()

                leader_dm_channel = await leader.create_dm()
                approval_view = JoinRequestApprovalView(party_id, user_requesting_join.id, self.bot,
                                                        self)  # type: ignore
                await leader_dm_channel.send(
                    f"Użytkownik {user_requesting_join.mention} (`{user_requesting_join.id}`) chce dołączyć do Twojego party: **{party_data['party_name']}**.",
                    view=approval_view
                )
                await interaction.followup.send("Twoja prośba o dołączenie została wysłana do lidera party.",
                                                ephemeral=True)
            except disnake.Forbidden:
                if user_requesting_join.id in party_data.get("pending_join_requests", []):  # Cofnij dodanie do pending
                    party_data["pending_join_requests"].remove(user_requesting_join.id)
                    save_party_data()
                await interaction.followup.send(
                    "Nie udało się wysłać prośby do lidera (prawdopodobnie ma zablokowane DM).", ephemeral=True)
            except Exception as e:
                if user_requesting_join.id in party_data.get("pending_join_requests", []):  # Cofnij dodanie do pending
                    party_data["pending_join_requests"].remove(user_requesting_join.id)
                    save_party_data()
                await interaction.followup.send(f"Wystąpił błąd przy wysyłaniu prośby: {e}", ephemeral=True)
                print(f"BŁĄD przycisku dołączania (party {party_id}, user {user_requesting_join.id}): {e}")

        elif custom_id.startswith("settings_leave_party_"):
            await interaction.response.defer(ephemeral=True)
            try:
                party_id = int(custom_id.split("_")[3])
            except (IndexError, ValueError):
                await interaction.followup.send("Błąd wewnętrzny przycisku.", ephemeral=True);
                return

            leaver = interaction.user
            party_data = active_parties.get(party_id)

            if not party_data:
                await interaction.followup.send("To party już nie istnieje.", ephemeral=True);
                return
            if leaver.id == party_data["leader_id"]:  # Lider nie może tak opuścić
                await interaction.followup.send(
                    "Lider nie może opuścić party w ten sposób. Użyj panelu lidera w DM (`!opusc` lub przycisk 'Rozwiąż Party').",
                    ephemeral=True);
                return
            if leaver.id not in party_data["member_ids"]:
                await interaction.followup.send("Nie jesteś członkiem tego party.", ephemeral=True);
                return

            guild = self.bot.get_guild(party_data["guild_id"])
            if not guild:  # Powinno być rzadkie
                await interaction.followup.send("Błąd serwera (gildia nieznaleziona).", ephemeral=True);
                return

            member_obj = guild.get_member(leaver.id)
            # Reszta logiki usuwania uprawnień i z party bez zmian
            channels_to_clear_perms_keys = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                            "voice_channel_id_2"]
            category_id = party_data.get("category_id")
            category_obj = guild.get_channel(category_id) if category_id else None

            if member_obj:  # Użytkownik wciąż jest na serwerze
                if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                    try:
                        await category_obj.set_permissions(member_obj, overwrite=None,
                                                           reason="Opuścił party (przycisk z kanału ustawień)")
                    except disnake.HTTPException as e:
                        print(
                            f"BŁĄD przy usuwaniu uprawnień dla {leaver.id} z kategorii {category_id} (party {party_id}): {e}")
                else:  # Fallback jeśli kategoria nie istnieje lub nie jest kategorią
                    for ch_key in channels_to_clear_perms_keys:
                        ch_id = party_data.get(ch_key)
                        if not ch_id: continue
                        channel = guild.get_channel(ch_id)
                        if channel:  # Upewnij się, że kanał istnieje
                            try:
                                await channel.set_permissions(member_obj, overwrite=None,
                                                              reason="Opuścił party (przycisk z kanału ustawień)")
                            except disnake.HTTPException as e:
                                print(
                                    f"BŁĄD przy usuwaniu uprawnień dla {leaver.id} z kanału {ch_id} (party {party_id}): {e}")
            # Niezależnie od tego, czy użytkownik jest na serwerze, usuń go z danych party
            if leaver.id in party_data["member_ids"]:
                party_data["member_ids"].remove(leaver.id)

            save_party_data()
            await self._update_party_emblem(party_id)
            await self._update_settings_embed(party_id)

            await interaction.followup.send(f"Pomyślnie opuściłeś/aś party '{party_data['party_name']}'.",
                                            ephemeral=True)

            leader_obj = self.bot.get_user(party_data["leader_id"])
            if not leader_obj:  # Spróbuj pobrać, jeśli nie ma w cache
                try:
                    leader_obj = await self.bot.fetch_user(party_data["leader_id"])
                except:
                    pass  # Ignoruj błąd, jeśli lider nieosiągalny

            if leader_obj:  # Jeśli udało się uzyskać obiekt lidera
                try:
                    await leader_obj.send(
                        f"Użytkownik {leaver.mention} (`{leaver.id}`) opuścił Twoje party '{party_data['party_name']}'.")
                except disnake.Forbidden:
                    pass  # Ignoruj, jeśli DM zablokowane
                if party_id in active_parties:  # Sprawdź, czy party wciąż istnieje (np. nie zostało rozwiązane w międzyczasie)
                    await self.send_leader_control_panel(leader_obj, party_id)


        elif custom_id.startswith("leader_disband_"):
            await interaction.response.defer(ephemeral=True)
            try:
                party_id = int(custom_id.split("_")[2])
            except (IndexError, ValueError):
                await interaction.followup.send("Błąd wewnętrzny przycisku 'Rozwiąż'.", ephemeral=True);
                return

            party_data_check = active_parties.get(party_id)
            if not party_data_check:
                await interaction.followup.send("To party już nie istnieje.", ephemeral=True);
                return
            if interaction.user.id != party_data_check["leader_id"]:
                await interaction.followup.send("Tylko lider może rozwiązać to party.", ephemeral=True);
                return

            await interaction.followup.send(f"Rozwiązywanie party '{party_data_check['party_name']}'...",
                                            ephemeral=True)  # Daj znać, że coś się dzieje
            await self.disband_party(party_id,
                                     reason=f"Rozwiązane przez lidera ({interaction.user.name}) za pomocą przycisku.")
            # Wiadomość o rozwiązaniu party jest wysyłana przez disband_party do lidera (jeśli to konieczne)

    async def _cleanup_dm_messages(self, ctx_or_interaction, bot_message: disnake.Message = None,
                                   user_message: disnake.Message = None, delay: int = None):
        effective_delay = delay if delay is not None else getattr(config, 'DM_MESSAGE_DELETE_DELAY',
                                                                  15)  # Użyj getattr dla bezpieczeństwa
        if user_message:
            try:
                await user_message.delete()
            except disnake.HTTPException:
                pass
        elif isinstance(ctx_or_interaction, commands.Context):  # Jeśli przekazano kontekst, usuń wiadomość komendy
            try:
                await ctx_or_interaction.message.delete()
            except disnake.HTTPException:
                pass
        # Nie usuwamy wiadomości interakcji, bo to może być wiadomość z przyciskiem

        if bot_message:
            if effective_delay > 0:
                await asyncio.sleep(effective_delay)
            try:
                await bot_message.delete()
            except disnake.HTTPException:
                pass

    @commands.command(name="opusc")
    @commands.dm_only()
    async def leave_party_dm_command(self, ctx: commands.Context, *, party_identifier: str):
        leaver = ctx.author
        bot_response_msg = None
        parties_member_of_and_not_leader = [{'id': pid, 'name': pdata.get("party_name", "N/A"), 'data': pdata} for
                                            pid, pdata in active_parties.items() if
                                            leaver.id in pdata.get("member_ids", []) and leaver.id != pdata.get(
                                                "leader_id")]

        if not parties_member_of_and_not_leader:
            bot_response_msg = await ctx.send(
                "Nie jesteś członkiem żadnego party, które mógłbyś opuścić tą komendą (jako zwykły członek). Jeśli jesteś liderem, musisz rozwiązać party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        target_party_id_to_leave = None
        target_party_data_to_leave = None

        # Sprawdź, czy identyfikator to liczba (ID party)
        if party_identifier.isdigit():
            party_id_candidate = int(party_identifier)
            for p_info in parties_member_of_and_not_leader:
                if p_info['id'] == party_id_candidate:
                    target_party_id_to_leave = party_id_candidate
                    target_party_data_to_leave = p_info['data'];
                    break
        else:  # Jeśli nie jest liczbą, szukaj po nazwie
            found_by_name = [p_info for p_info in parties_member_of_and_not_leader if  # Poprawka: p_in_list -> p_info
                             p_info['name'].lower() == party_identifier.lower().strip()]
            if len(found_by_name) == 1:
                target_party_id_to_leave = found_by_name[0]['id']
                target_party_data_to_leave = found_by_name[0]['data']
            elif len(found_by_name) > 1:
                options = "\n".join([f"- `{p['id']}` : {p['name']}" for p in found_by_name])
                delay_multiplier = getattr(config, 'DM_MESSAGE_DELETE_DELAY', 15) * 2
                bot_response_msg = await ctx.send(f"Jesteś członkiem kilku party o tej nazwie. Podaj ID:\n{options}")
                await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg, delay=delay_multiplier);
                return

        if not target_party_id_to_leave or not target_party_data_to_leave:
            bot_response_msg = await ctx.send(
                f"Nie znaleziono party '{party_identifier}', którego jesteś członkiem (i nie liderem).")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        guild = self.bot.get_guild(target_party_data_to_leave["guild_id"])
        if not guild:
            bot_response_msg = await ctx.send("Błąd: Serwer party nieosiągalny.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        member_obj = guild.get_member(leaver.id)  # członek opuszczający party

        if member_obj:  # Jeśli użytkownik jest na serwerze, usuń uprawnienia
            category_id = target_party_data_to_leave.get("category_id")
            category_obj = guild.get_channel(category_id) if category_id else None
            if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                try:
                    await category_obj.set_permissions(member_obj, overwrite=None, reason="Opuścił party (komenda DM)")
                except disnake.HTTPException:
                    pass
            else:  # Fallback jeśli kategoria nie istnieje lub nie jest kategorią
                channels_to_clear_keys = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                          "voice_channel_id_2"]
                for ch_key in channels_to_clear_keys:
                    ch_id = target_party_data_to_leave.get(ch_key)
                    if ch_id:
                        channel = guild.get_channel(ch_id)
                        if channel:
                            try:
                                await channel.set_permissions(member_obj, overwrite=None,
                                                              reason="Opuścił party (komenda DM)")
                            except disnake.HTTPException:
                                pass
        # Usuń z listy członków
        if leaver.id in target_party_data_to_leave["member_ids"]:
            target_party_data_to_leave["member_ids"].remove(leaver.id)
        save_party_data()
        await self._update_party_emblem(target_party_id_to_leave)
        if target_party_data_to_leave.get("settings_channel_id"):
            await self._update_settings_embed(target_party_id_to_leave)

        bot_response_msg = await ctx.send(f"Pomyślnie opuściłeś/aś party '{target_party_data_to_leave['party_name']}'.")

        leader_of_left_party = self.bot.get_user(target_party_data_to_leave["leader_id"])
        if not leader_of_left_party:  # Spróbuj pobrać, jeśli nie ma w cache
            try:
                leader_of_left_party = await self.bot.fetch_user(target_party_data_to_leave["leader_id"])
            except:
                pass

        if leader_of_left_party:
            try:
                await leader_of_left_party.send(
                    f"Użytkownik {leaver.mention} (`{leaver.id}`) opuścił Twoje party '{target_party_data_to_leave['party_name']}'.")
            except disnake.Forbidden:
                pass
            if target_party_id_to_leave in active_parties:  # Sprawdź czy party wciąż istnieje
                await self.send_leader_control_panel(leader_of_left_party, target_party_id_to_leave)

        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @leave_party_dm_command.error
    async def leave_party_dm_command_error_handler(self, ctx, error):
        bot_response_msg = None
        if isinstance(error, commands.MissingRequiredArgument):
            if error.param.name == 'party_identifier':
                bot_response_msg = await ctx.send(
                    f"Musisz podać ID lub nazwę party, np. `{config.DEFAULT_COMMAND_PREFIX}opusc MojeParty`.")
        elif isinstance(error, commands.PrivateMessageOnly):
            pass  # To jest oczekiwane
        else:
            bot_response_msg = await ctx.send(f"Błąd w !opusc: {type(error).__name__} - {error}")
            print(f"BŁĄD w !opusc (DM): {error}")
        if bot_response_msg:
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @commands.command(name="usun_czlonka")
    @commands.dm_only()
    async def remove_member_dm_command(self, ctx: commands.Context, *, member_identifier: str):
        leader = ctx.author
        # Znajdź party, którego liderem jest autor komendy
        party_id_led_by_author = next(
            (pid for pid, pdata in active_parties.items() if pdata.get("leader_id") == leader.id), None)
        bot_response_msg = None

        if not party_id_led_by_author:
            bot_response_msg = await ctx.send("Nie jesteś liderem żadnego aktywnego party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        party_id = party_id_led_by_author
        party_data = active_parties.get(party_id)  # Powinno istnieć, skoro znaleziono party_id

        target_user_id = None
        # Próba parsowania wzmianki lub ID
        if member_identifier.startswith('<@') and member_identifier.endswith('>'):
            try:
                target_user_id = int(member_identifier.strip('<@!>'))  # ! dla nickname'ów
            except ValueError:
                pass
        elif member_identifier.isdigit():
            try:
                target_user_id = int(member_identifier)
            except ValueError:
                pass

        if not target_user_id:
            bot_response_msg = await ctx.send("Niepoprawny format identyfikatora. Podaj @wzmiankę lub ID użytkownika.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        if target_user_id == leader.id:
            bot_response_msg = await ctx.send(
                "Nie możesz usunąć siebie jako lidera w ten sposób. Możesz rozwiązać party używając przycisku w tym panelu.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        if target_user_id not in party_data.get("member_ids", []):
            bot_response_msg = await ctx.send("Tego użytkownika nie ma w Twoim party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        guild = self.bot.get_guild(party_data["guild_id"])
        if not guild:
            bot_response_msg = await ctx.send("Błąd: Serwer party nieosiągalny.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        member_to_remove_obj = guild.get_member(target_user_id)
        removed_user_mention_or_id = f"ID `{target_user_id}`"  # Domyślnie
        if member_to_remove_obj:
            removed_user_mention_or_id = f"{member_to_remove_obj.mention} (`{target_user_id}`)"

        # Usuwanie uprawnień, jeśli użytkownik jest na serwerze
        if member_to_remove_obj:
            category_id = party_data.get("category_id")
            category_obj = guild.get_channel(category_id) if category_id else None
            if category_obj and isinstance(category_obj, disnake.CategoryChannel):
                try:
                    await category_obj.set_permissions(member_to_remove_obj, overwrite=None,
                                                       reason="Usunięty z party przez lidera")
                except disnake.HTTPException:
                    pass
            else:
                channels_to_clear_keys = ["settings_channel_id", "text_channel_id", "voice_channel_id",
                                          "voice_channel_id_2"]
                for ch_key in channels_to_clear_keys:
                    ch_id = party_data.get(ch_key)
                    if ch_id:
                        channel = guild.get_channel(ch_id)
                        if channel:
                            try:
                                await channel.set_permissions(member_to_remove_obj, overwrite=None,
                                                              reason="Usunięty z party przez lidera")
                            except disnake.HTTPException:
                                pass
        # Usuń z listy członków
        if target_user_id in party_data.get("member_ids", []):
            party_data["member_ids"].remove(target_user_id)

        save_party_data()
        await self._update_party_emblem(party_id)
        if party_data.get("settings_channel_id"):
            await self._update_settings_embed(party_id)

        await self.send_leader_control_panel(leader, party_id)  # Odśwież panel lidera
        bot_response_msg = await ctx.send(
            f"{removed_user_mention_or_id} został usunięty z party '{party_data['party_name']}'.")

        if member_to_remove_obj:  # Wyślij powiadomienie do usuniętego użytkownika
            try:
                await member_to_remove_obj.send(
                    f"Zostałeś/aś usunięty/a z party '{party_data['party_name']}' przez lidera.")
            except disnake.Forbidden:
                pass  # Ignoruj, jeśli DM zablokowane

        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @remove_member_dm_command.error
    async def remove_member_dm_command_error_handler(self, ctx, error):
        bot_response_msg = None
        if isinstance(error, commands.MissingRequiredArgument):
            if error.param.name == 'member_identifier':
                bot_response_msg = await ctx.send(
                    f"Musisz podać @wzmiankę lub ID użytkownika do usunięcia, np. `{config.DEFAULT_COMMAND_PREFIX}usun_czlonka @Uzytkownik`.")
        elif isinstance(error, commands.PrivateMessageOnly):
            pass
        else:
            bot_response_msg = await ctx.send(f"Błąd w !usun_czlonka: {type(error).__name__} - {error}")
            print(f"BŁĄD w !usun_czlonka: {error}")
        if bot_response_msg:
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @commands.command(name="zmien_nazwe_party")
    @commands.dm_only()
    async def rename_party_dm_command(self, ctx: commands.Context, *, new_name: str):
        leader = ctx.author
        party_id_led_by_author = next(
            (pid for pid, pdata in active_parties.items() if pdata.get("leader_id") == leader.id), None)
        bot_response_msg = None

        if not party_id_led_by_author:
            bot_response_msg = await ctx.send("Nie jesteś liderem żadnego aktywnego party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        party_id = party_id_led_by_author
        party_data = active_parties.get(party_id)  # Powinno istnieć

        new_name_stripped = new_name.strip()
        if not new_name_stripped:
            bot_response_msg = await ctx.send("Nowa nazwa party nie może być pusta.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        max_len = getattr(config, 'MAX_PARTY_NAME_LENGTH', 50)  # Domyślna maksymalna długość
        if not (0 < len(new_name_stripped) <= max_len):
            bot_response_msg = await ctx.send(f"Nazwa musi mieć od 1 do {max_len} znaków.")
            delay = getattr(config, 'DM_MESSAGE_DELETE_DELAY', 15) * 1.5
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg, delay=int(delay));
            return

        if new_name_stripped == party_data["party_name"]:
            bot_response_msg = await ctx.send(
                f"Nowa nazwa jest taka sama jak obecna ('{new_name_stripped}'). Nie dokonano zmian.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        old_name = party_data["party_name"]
        party_data["party_name"] = new_name_stripped

        guild = self.bot.get_guild(party_data["guild_id"])
        leader_display_name_for_cat = leader.display_name  # Domyślnie
        if guild:
            leader_member_obj = guild.get_member(leader.id)
            if leader_member_obj: leader_display_name_for_cat = leader_member_obj.display_name

            category = guild.get_channel(party_data["category_id"]) if party_data.get("category_id") else None
            if category and isinstance(category, disnake.CategoryChannel):
                try:
                    await category.edit(name=f"🎉 {new_name_stripped} ({leader_display_name_for_cat})",
                                        reason=f"Zmiana nazwy party przez lidera {leader.id}")
                except disnake.HTTPException as e:
                    print(f"WARN: Zmiana nazwy kategorii dla party {party_id} nie powiodła się: {e}")
            # Zmiana nazw kanałów podrzędnych
            channel_configs = [
                ("settings_channel_id", f"📌︱info-{new_name_stripped[:20]}"),
                ("text_channel_id", f"💬︱{new_name_stripped[:20]}"),
                ("voice_channel_id", f"🔊︱Głos 1 ({new_name_stripped[:15]})"),
                ("voice_channel_id_2", f"🔊︱Głos 2 ({new_name_stripped[:15]})")
            ]
            for ch_key, ch_new_name_format in channel_configs:
                ch_id = party_data.get(ch_key)
                if ch_id:
                    channel_obj = guild.get_channel(ch_id)
                    if channel_obj:
                        try:
                            await channel_obj.edit(name=ch_new_name_format,
                                                   reason=f"Zmiana nazwy party przez lidera {leader.id}")
                        except disnake.HTTPException as e:
                            print(f"WARN: Nie udało się zmienić nazwy kanału {ch_key} ({ch_id}): {e}")
        save_party_data()
        await self._update_party_emblem(party_id)
        if party_data.get("settings_channel_id"):
            await self._update_settings_embed(party_id)

        await self.send_leader_control_panel(leader, party_id)  # Odśwież panel
        bot_response_msg = await ctx.send(f"Nazwa party zmieniona z '{old_name}' na '{new_name_stripped}'.")
        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @rename_party_dm_command.error
    async def rename_party_dm_command_error_handler(self, ctx, error):
        bot_response_msg = None
        if isinstance(error, commands.MissingRequiredArgument):
            if error.param.name == 'new_name':
                bot_response_msg = await ctx.send(
                    f"Podaj nową nazwę dla party, np. `{config.DEFAULT_COMMAND_PREFIX}zmien_nazwe_party Moje Nowe Super Party`.")
        elif isinstance(error, commands.PrivateMessageOnly):
            pass
        else:
            bot_response_msg = await ctx.send(f"Błąd w !zmien_nazwe_party: {type(error).__name__} - {error}")
            print(f"BŁĄD w !zmien_nazwe_party: {error}")
        if bot_response_msg:
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @commands.command(name="lista_czlonkow", aliases=["panel", "refreshpanel", "panelparty"])
    @commands.dm_only()
    async def list_members_dm_command(self, ctx: commands.Context):
        leader = ctx.author
        party_id_led_by_author = next(
            (pid for pid, pdata in active_parties.items() if pdata.get("leader_id") == leader.id), None)
        bot_response_msg = None

        if not party_id_led_by_author:
            bot_response_msg = await ctx.send("Nie jesteś liderem żadnego aktywnego party.")
            await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg);
            return

        party_id = party_id_led_by_author
        await self.send_leader_control_panel(leader, party_id)  # To wysyła nowy/zaktualizowany panel
        bot_response_msg = await ctx.send("Panel zarządzania party został odświeżony/wysłany ponownie.")
        await self._cleanup_dm_messages(ctx, bot_message=bot_response_msg)

    @tasks.loop(minutes=config.EXTENSION_CHECK_LOOP_MINUTES if hasattr(config, 'EXTENSION_CHECK_LOOP_MINUTES') else 5.0)
    async def extension_check_loop(self):
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        p_ids_iter = list(active_parties.keys())  # Iteruj po kopii kluczy, bo słownik może się zmieniać

        for p_id in p_ids_iter:
            p_data = active_parties.get(p_id)  # Pobierz świeże dane na wypadek zmian
            if not p_data: continue  # Party mogło zostać rozwiązane w międzyczasie

            if now_ts >= p_data["expiry_timestamp"]:
                await self.disband_party(p_id,
                                         reason=f"Party automatycznie wygasło <t:{int(p_data['expiry_timestamp'])}:F>.")
                continue

            # Sprawdzenie, czy należy wysłać przypomnienie
            # Użyj getattr dla bezpiecznego dostępu do konfiguracji
            ext_reminder_hours = getattr(config, 'EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY', 1)
            ext_window_hours = getattr(config, 'EXTENSION_WINDOW_HOURS', 1)

            # Upewnij się, że next_reminder_timestamp istnieje i jest liczbą
            next_reminder_ts = p_data.get("next_reminder_timestamp")
            if not isinstance(next_reminder_ts, (int, float)):
                # Ustaw domyślny, jeśli brakuje lub jest niepoprawny, aby uniknąć błędu
                # To powinno być ustawione przy tworzeniu/przedłużaniu party
                p_data["next_reminder_timestamp"] = p_data["expiry_timestamp"] - datetime.timedelta(
                    hours=ext_reminder_hours).total_seconds()
                next_reminder_ts = p_data["next_reminder_timestamp"]

            should_send_reminder = (
                    not p_data.get("reminder_sent_for_current_cycle", False) and
                    p_id not in parties_awaiting_extension_reply and  # Nie wysyłaj, jeśli już czeka na odpowiedź
                    now_ts >= next_reminder_ts and  # Nadszedł czas na przypomnienie
                    p_data["expiry_timestamp"] > now_ts  # Party wciąż aktywne
            )

            if should_send_reminder:
                ldr = self.bot.get_user(p_data["leader_id"])
                if not ldr:  # Spróbuj pobrać, jeśli nie ma w cache
                    try:
                        ldr = await self.bot.fetch_user(p_data["leader_id"])
                    except (disnake.NotFound, disnake.HTTPException):
                        print(f"WARN LOOP: Lider party {p_id} nieosiągalny. Party wygaśnie normalnie.")
                        continue  # Przejdź do następnego party

                try:
                    reply_due_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                        hours=ext_window_hours)
                    reply_due_ts = reply_due_dt.timestamp()
                    dm_ch = await ldr.create_dm()  # type: ignore

                    party_extend_hours = getattr(config, 'PARTY_EXTEND_BY_HOURS', 2)
                    reminder_msg_content = (
                        f"🔔 Przypomnienie!\nTwoje party **'{p_data['party_name']}'** wygasa <t:{int(p_data['expiry_timestamp'])}:R>.\n"
                        f"Przedłużyć o **{party_extend_hours}**h? Odpisz `Tak`/`Nie` do <t:{int(reply_due_ts)}:R>."
                    )
                    reminder_dm_msg = await dm_ch.send(reminder_msg_content)

                    parties_awaiting_extension_reply[p_id] = {
                        'reply_due_ts': reply_due_ts,
                        'leader_dm_channel_id': dm_ch.id,
                        'reminder_message_id': reminder_dm_msg.id
                    }
                    p_data["reminder_sent_for_current_cycle"] = True
                    p_data["extension_reminder_dm_id"] = reminder_dm_msg.id  # Zapisz ID wiadomości z przypomnieniem
                    save_party_data()
                    print(f"INFO LOOP: Wysłano przypomnienie o przedłużeniu do lidera party {p_id}.")
                except disnake.Forbidden:
                    print(
                        f"WARN LOOP: Nie udało się wysłać DM z przypomnieniem do lidera {ldr.id if ldr else p_data['leader_id']} dla party {p_id}.")  # type: ignore
                except Exception as e:
                    print(f"BŁĄD LOOP podczas wysyłania przypomnienia dla party {p_id}: {e}")

            # Sprawdzenie, czy upłynął czas na odpowiedź
            if p_id in parties_awaiting_extension_reply and now_ts >= parties_awaiting_extension_reply[p_id][
                'reply_due_ts']:
                ldr = self.bot.get_user(p_data["leader_id"])
                if not ldr:  # Spróbuj pobrać
                    try:
                        ldr = await self.bot.fetch_user(p_data["leader_id"])
                    except:
                        pass  # Ignoruj błąd, jeśli lider nieosiągalny

                reminder_info = parties_awaiting_extension_reply[p_id]
                # Usuń wiadomość z przypomnieniem, jeśli istnieje
                if reminder_info.get('leader_dm_channel_id') and reminder_info.get('reminder_message_id'):
                    try:
                        dm_ch_for_cleanup = self.bot.get_channel(reminder_info['leader_dm_channel_id']) or \
                                            await self.bot.fetch_channel(reminder_info['leader_dm_channel_id'])
                        if isinstance(dm_ch_for_cleanup, disnake.DMChannel):
                            msg_to_delete = await dm_ch_for_cleanup.fetch_message(reminder_info['reminder_message_id'])
                            await msg_to_delete.delete()
                    except (disnake.NotFound, disnake.Forbidden, disnake.HTTPException):
                        pass  # Ignoruj błędy

                del parties_awaiting_extension_reply[p_id]  # Usuń z oczekujących
                p_data["extension_reminder_dm_id"] = None  # Zresetuj ID wiadomości przypomnienia
                # Nie resetuj reminder_sent_for_current_cycle, bo cykl się zakończył brakiem odpowiedzi

                if ldr:  # Jeśli udało się uzyskać obiekt lidera
                    try:
                        dm_message_delete_delay = getattr(config, 'DM_MESSAGE_DELETE_DELAY', 15) * 2
                        await ldr.send(
                            f"Nie otrzymano odpowiedzi ws. przedłużenia party '{p_data['party_name']}'. Wygasnie <t:{int(p_data['expiry_timestamp'])}:R>.",
                            delete_after=dm_message_delete_delay
                        )
                    except disnake.Forbidden:
                        pass  # Ignoruj, jeśli DM zablokowane
                print(f"INFO LOOP: Lider party {p_id} nie odpowiedział na czas. Party wygaśnie normalnie.")
                save_party_data()  # Zapisz zmiany w p_data

    @extension_check_loop.before_loop
    async def before_extension_check_loop_func(self):
        await self.bot.wait_until_ready()
        print("Pętla sprawdzania przedłużeń party jest gotowa.")

    @commands.Cog.listener("on_message")
    async def on_extension_reply(self, message: disnake.Message):
        if message.author.bot or message.guild is not None: return  # Tylko DM od użytkowników

        author_id = message.author.id
        party_id_being_processed = None
        extension_data_for_party = None

        # Znajdź party, na którego odpowiedź czeka ten użytkownik w tym kanale DM
        for pid, ext_data in list(parties_awaiting_extension_reply.items()):  # Iteruj po kopii
            p_data_check = active_parties.get(pid)
            if p_data_check and \
                    p_data_check.get("leader_id") == author_id and \
                    ext_data.get('leader_dm_channel_id') == message.channel.id:
                # Sprawdź, czy odpowiedź jest na czas
                if datetime.datetime.now(datetime.timezone.utc).timestamp() < ext_data['reply_due_ts']:
                    party_id_being_processed = pid
                    extension_data_for_party = ext_data
                    break  # Znaleziono party, przerwij pętlę
                else:  # Odpowiedź spóźniona
                    if ext_data.get('reminder_message_id'):  # Usuń oryginalne przypomnienie
                        try:
                            msg_to_del = await message.channel.fetch_message(ext_data['reminder_message_id'])
                            await msg_to_del.delete()
                        except:
                            pass  # Ignoruj błędy
                    try:
                        await message.delete()  # Usuń spóźnioną odpowiedź użytkownika
                    except:
                        pass
                    try:
                        delete_delay = getattr(config, 'DM_MESSAGE_DELETE_DELAY', 15) * 2
                        await message.channel.send(
                            f"Odpowiedź ('{message.content}') dla party '{p_data_check.get('party_name', 'N/A')}' przyszła po czasie. Party wygaśnie zgodnie z planem.",
                            delete_after=delete_delay)
                    except:
                        pass
                    del parties_awaiting_extension_reply[pid]  # Usuń z oczekujących
                    if p_data_check:  # Upewnij się, że party data wciąż istnieje
                        p_data_check["extension_reminder_dm_id"] = None
                        save_party_data()
                    return  # Zakończ przetwarzanie, bo odpowiedź była spóźniona

        if not party_id_being_processed or not extension_data_for_party:
            return  # Brak oczekującej odpowiedzi od tego użytkownika w tym DM lub dla istniejącego party

        p_data = active_parties.get(party_id_being_processed)
        if not p_data:  # Party mogło zostać rozwiązane w międzyczasie
            if party_id_being_processed in parties_awaiting_extension_reply:
                del parties_awaiting_extension_reply[party_id_being_processed]
            return

        reply_content = message.content.strip().lower()
        bot_response_after_reply_msg = None
        user_reply_msg = message  # Wiadomość od użytkownika (`Tak`/`Nie` lub błędna)

        # Usuń oryginalną wiadomość z przypomnieniem od bota
        if extension_data_for_party.get('reminder_message_id'):
            try:
                original_reminder_msg = await message.channel.fetch_message(
                    extension_data_for_party['reminder_message_id'])
                await original_reminder_msg.delete()
            except:
                pass  # Ignoruj błędy

        party_extend_hours = getattr(config, 'PARTY_EXTEND_BY_HOURS', 2)
        ext_reminder_hours = getattr(config, 'EXTENSION_REMINDER_HOURS_BEFORE_EXPIRY', 1)
        lifespan_hours = getattr(config, 'PARTY_LIFESPAN_HOURS', 4)  # Potrzebne do logiki next_reminder_timestamp

        if reply_content == "tak":
            new_expiry_ts = p_data["expiry_timestamp"] + datetime.timedelta(hours=party_extend_hours).total_seconds()
            p_data["expiry_timestamp"] = new_expiry_ts
            # Oblicz nowy czas następnego przypomnienia
            next_rem_ts_after_extend = new_expiry_ts - datetime.timedelta(hours=ext_reminder_hours).total_seconds()
            # Jeśli pełny cykl życia jest krótszy niż czas do przypomnienia, przypomnienie jest "natychmiast" przed wygaśnięciem
            # Ta logika może wymagać przemyślenia - czy zawsze resetujemy cykl?
            if party_extend_hours <= ext_reminder_hours:  # Jeśli przedłużenie jest krótsze niż okno przypomnienia
                p_data[
                    "next_reminder_timestamp"] = new_expiry_ts  # Ustaw na nowy czas wygaśnięcia, aby nie wysyłać od razu
            else:
                p_data["next_reminder_timestamp"] = next_rem_ts_after_extend

            p_data["reminder_sent_for_current_cycle"] = False  # Zresetuj flagę dla nowego cyklu przedłużenia
            p_data["extension_reminder_dm_id"] = None  # Zresetuj ID wiadomości przypomnienia
            del parties_awaiting_extension_reply[party_id_being_processed]  # Usuń z oczekujących
            save_party_data()
            bot_response_after_reply_msg = await message.channel.send(
                f"Party **'{p_data['party_name']}'** przedłużone o {party_extend_hours}h! Nowy czas wygaśnięcia: <t:{int(new_expiry_ts)}:F> (<t:{int(new_expiry_ts)}:R>).")
            await self.send_leader_control_panel(message.author, party_id_being_processed)  # Odśwież panel
            print(f"INFO REPLY: Party {party_id_being_processed} przedłużone przez lidera.")
        elif reply_content == "nie":
            p_data["extension_reminder_dm_id"] = None  # Zresetuj ID wiadomości przypomnienia
            # Nie zmieniamy expiry_timestamp ani reminder_sent_for_current_cycle (bo cykl się kończy)
            del parties_awaiting_extension_reply[party_id_being_processed]  # Usuń z oczekujących
            save_party_data()
            bot_response_after_reply_msg = await message.channel.send(
                f"Nie przedłużono party **'{p_data['party_name']}'**. Wygasnie <t:{int(p_data['expiry_timestamp'])}:R>.")
            print(f"INFO REPLY: Lider nie przedłużył party {party_id_being_processed}.")
        else:  # Niepoprawna odpowiedź
            current_reply_due_ts = extension_data_for_party['reply_due_ts']  # Zachowaj oryginalny czas odpowiedzi
            new_reminder_content = (
                f"⚠️ Nieprawidłowa odpowiedź: '{message.content}'.\n"
                f"Party **'{p_data['party_name']}'** wygasa <t:{int(p_data['expiry_timestamp'])}:R>.\n"
                f"Przedłużyć o **{party_extend_hours}**h? Odpisz `Tak`/`Nie` do <t:{int(current_reply_due_ts)}:R>."
            )
            try:
                # Wyślij ponownie pytanie, ale nie zmieniaj `reply_due_ts` w `parties_awaiting_extension_reply`
                new_reminder_msg = await message.channel.send(new_reminder_content)
                # Zaktualizuj ID wiadomości w `parties_awaiting_extension_reply` i `p_data`
                parties_awaiting_extension_reply[party_id_being_processed]['reminder_message_id'] = new_reminder_msg.id
                p_data["extension_reminder_dm_id"] = new_reminder_msg.id
                save_party_data()
                bot_response_after_reply_msg = None  # Nie usuwamy wiadomości bota, bo to nowe pytanie
            except disnake.HTTPException as e:
                print(
                    f"BŁĄD REPLY: Nie udało się wysłać ponownego przypomnienia dla party {party_id_being_processed}: {e}")
                # Jeśli nie udało się wysłać, zostawiamy stan bez zmian, użytkownik może spróbować odpisać na stare (jeśli nie usunięte)

        # Usuń wiadomość użytkownika (np. "Tak", "Nie") i ewentualnie odpowiedź bota (np. "Przedłużono")
        delete_delay = getattr(config, 'DM_MESSAGE_DELETE_DELAY', 15)
        await self._cleanup_dm_messages(None, bot_message=bot_response_after_reply_msg, user_message=user_reply_msg,
                                        delay=delete_delay)


def setup(bot: commands.Bot):
    cog_instance = PartyManagementCog(bot)
    bot.add_cog(cog_instance)
    print(f"Cog '{cog_instance.qualified_name}' został pomyślnie załadowany i dodany do bota.")