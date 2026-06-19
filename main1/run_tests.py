#!/usr/bin/env python3
import subprocess
import re
import os

bots = [
    ('example_bot', './example_bot.py'),
    ('montebot', './montebot.py'),
    ('speedbot', './speedbot.py'),
    ('speedbotv2', './speedbotv2.py'),
    ('gtobot', './gtobot.py'),
    ('smartbot', './smartbot.py'),
    ('he_bot', './he_bot.py'),
]

results = []

for bot_name, bot_file in bots:
    print(f"\n{'='*60}")
    print(f"Testing mccfr_v2 vs {bot_name}")
    print('='*60)
    
    # Update config.py
    config_content = f"""PYTHON_CMD = "python3"

BOT_1_NAME = 'mccfr_v2'
BOT_1_FILE = './mccfr_v2.py'

BOT_2_NAME = '{bot_name}'
BOT_2_FILE= '{bot_file}'

GAME_LOG_FOLDER = './logs'
"""
    
    with open('config.py', 'w') as f:
        f.write(config_content)
    
    # Run engine
    try:
        result = subprocess.run(['python3', 'engine.py'], 
                              capture_output=True, 
                              text=True, 
                              timeout=120)
        
        output = result.stdout + result.stderr
        
        # Extract bankroll - look for "Total Bankroll:" line for mccfr_v2
        lines = output.split('\n')
        bankroll = None
        for i, line in enumerate(lines):
            if 'mccfr_v2' in line and i + 5 < len(lines):
                # Look for Total Bankroll in next few lines
                for j in range(i, min(i+10, len(lines))):
                    if 'Total Bankroll:' in lines[j]:
                        match = re.search(r'Total Bankroll:\s*(-?\d+)', lines[j])
                        if match:
                            bankroll = int(match.group(1))
                            break
                if bankroll is not None:
                    break
        
        if bankroll is not None:
            results.append((bot_name, bankroll))
            print(f"✓ mccfr_v2 bankroll: {bankroll:+d}")
        else:
            results.append((bot_name, "ERROR"))
            print(f"✗ Could not extract bankroll")
            print(output[-500:])
            
    except subprocess.TimeoutExpired:
        results.append((bot_name, "TIMEOUT"))
        print(f"✗ Test timed out")
    except Exception as e:
        results.append((bot_name, f"ERROR: {e}"))
        print(f"✗ Error: {e}")

# Print summary
print(f"\n\n{'='*60}")
print("SUMMARY: mccfr_v2 vs All Bots")
print('='*60)
print(f"{'Opponent':<20} {'mccfr_v2 Bankroll':>20}")
print('-'*60)

for bot_name, bankroll in results:
    if isinstance(bankroll, int):
        print(f"{bot_name:<20} {bankroll:>+20d}")
    else:
        print(f"{bot_name:<20} {str(bankroll):>20}")

print('='*60)
