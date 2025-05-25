# party_bot/main.py

import disnake
from disnake.ext import commands
import os
import traceback

try:
    import config
except ModuleNotFoundError:
    print("BŁĄD KRYTYCZNY: Plik config.py nie został znaleziony!")
    print("Upewnij się, że plik config.py istnieje w tym samym katalogu co main.py.")
    exit()

intents_config = disnake.Intents.default()
intents_config.messages = True
intents_config.guilds = True
intents_config.reactions = True
intents_config.message_content = True
intents_config.members = True

bot = commands.Bot(command_prefix=config.DEFAULT_COMMAND_PREFIX, intents=intents_config)

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user.name} (ID: {bot.user.id})')
    print(f"Bot połączony z {len(bot.guilds)} serwerami.")
    print(f"Prefix komend: {config.DEFAULT_COMMAND_PREFIX}")
    activity = disnake.Activity(type=disnake.ActivityType.watching, name="aktywnosc party!")
    await bot.change_presence(activity=activity)
    print("Bot jest gotowy do działania.")
    print("------")

# --- NOWE, POPRAWIONE ŁADOWANIE COGÓW ---
# Ładujemy tylko konkretny Cog, a nie wszystko z katalogu
COGS_TO_LOAD = [
    'cogs.party_manager'  # To jest jedyny plik w katalogu 'cogs', który definiuje Cog i funkcję setup
]

print("Rozpoczynanie ładowania cogów...")
for cog_module_path in COGS_TO_LOAD:
    try:
        bot.load_extension(cog_module_path)
        print(f"Pomyślnie załadowano cog: {cog_module_path}")
    except commands.ExtensionAlreadyLoaded:
        print(f"INFO: Cog {cog_module_path} jest już załadowany.")
    except commands.ExtensionNotFound:
        print(f"BŁĄD: Nie znaleziono coga {cog_module_path}. Sprawdź ścieżkę i nazwę pliku.")
    except commands.NoEntryPointError:
        print(f"BŁĄD: W cogu {cog_module_path} brakuje funkcji setup(bot).")
    except Exception as e:
        print(f"Nie udało się załadować coga {cog_module_path}.")
        traceback.print_exc() # Wyświetl pełny błąd
print("Zakończono ładowanie cogów.")
# --- KONIEC NOWEGO ŁADOWANIA COGÓW ---

if __name__ == "__main__":
    if config.BOT_TOKEN == "TWOJ_BOT_TOKEN_TUTAJ" or not config.BOT_TOKEN:
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        print("!!! BŁĄD: Token bota nie został ustawiony w pliku config.py.          !!!")
        # ... (reszta komunikatu o błędzie tokenu) ...
        print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    else:
        bot.run(config.BOT_TOKEN)
