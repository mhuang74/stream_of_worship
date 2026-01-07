#!/usr/bin/env python3
"""
Transition Review CLI: Worship Music Transition System

Version: 2.0.0
Date: 2026-01-05
Purpose: Interactive CLI for reviewing and rating section transitions

Features:
- Load transitions from master index
- Play audio variants with controls
- Collect structured feedback and ratings
- Save progress persistently
- Export summary CSV
"""

import warnings
warnings.filterwarnings('ignore')

# Audio playback
import sounddevice as sd
import soundfile as sf

# Data handling
import numpy as np
import pandas as pd
import json
from pathlib import Path
from datetime import datetime
import sys
import threading
import time

# Configuration
OUTPUT_DIR = Path("poc_output_allinone")
TRANSITIONS_DIR = OUTPUT_DIR / "section_transitions"
METADATA_DIR = TRANSITIONS_DIR / "metadata"
INDEX_FILE = METADATA_DIR / "transitions_index.json"
PROGRESS_FILE = METADATA_DIR / "review_progress.json"

# Global playback state
playback_state = {
    'playing': False,
    'stream': None,
    'current_file': None,
    'stop_requested': False
}


# =============================================================================
# TRANSITIONS INDEX MANAGEMENT
# =============================================================================

def load_transitions_index():
    """
    Load the master transitions index from JSON.

    Returns:
        Dict with transitions index data, or None if not found
    """
    if not INDEX_FILE.exists():
        print(f"❌ ERROR: Transitions index not found at: {INDEX_FILE}")
        print(f"   Please run generate_section_transitions.py first.")
        return None

    try:
        with open(INDEX_FILE, 'r') as f:
            index = json.load(f)

        print(f"✓ Loaded transitions index")
        print(f"  Schema version: {index.get('schema_version', 'unknown')}")
        print(f"  Total transitions: {index['statistics']['total_transitions']}")
        print(f"  Total storage: {index['statistics']['total_storage_mb']:.2f} MB")

        return index

    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ ERROR: Failed to load transitions index: {e}")
        return None


def save_transitions_index(index):
    """
    Save the master transitions index to JSON (atomic write).

    Args:
        index: Complete transitions index dict
    """
    try:
        # Update statistics
        index['statistics']['reviewed_count'] = sum(
            1 for t in index['transitions']
            if t['review']['status'] in ['reviewed', 'approved', 'rejected']
        )
        index['statistics']['approved_count'] = sum(
            1 for t in index['transitions']
            if t['review']['status'] == 'approved'
        )

        # Write to temporary file first (atomic write pattern)
        temp_file = INDEX_FILE.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(index, f, indent=2)

        # Move to final location
        temp_file.replace(INDEX_FILE)

        print(f"✓ Saved transitions index")

    except (IOError, OSError) as e:
        print(f"❌ ERROR: Failed to save transitions index: {e}")


# =============================================================================
# AUDIO PLAYBACK
# =============================================================================

def play_audio(filepath, blocking=False):
    """
    Play audio file with simple controls.

    Args:
        filepath: Path to audio file
        blocking: If True, block until playback finishes

    Returns:
        True if playback started, False otherwise
    """
    global playback_state

    # Stop any currently playing audio
    stop_playback()

    try:
        # Load audio file
        audio_data, sample_rate = sf.read(filepath)

        print(f"\n▶ Playing: {filepath.name}")
        print(f"  Duration: {len(audio_data) / sample_rate:.1f}s | Press Ctrl+C to stop")

        playback_state['playing'] = True
        playback_state['current_file'] = str(filepath)
        playback_state['stop_requested'] = False

        # Play audio
        if blocking:
            sd.play(audio_data, sample_rate)
            sd.wait()
            playback_state['playing'] = False
        else:
            # Non-blocking playback
            sd.play(audio_data, sample_rate)

        return True

    except Exception as e:
        print(f"❌ ERROR: Failed to play audio: {e}")
        playback_state['playing'] = False
        return False


def stop_playback():
    """Stop any currently playing audio."""
    global playback_state

    if playback_state['playing']:
        sd.stop()
        playback_state['playing'] = False
        playback_state['stop_requested'] = True
        print(f"\n⏹ Stopped playback")


# =============================================================================
# REVIEW INTERFACE
# =============================================================================

def display_transition_info(transition, index):
    """
    Display detailed information about a transition.

    Args:
        transition: Transition metadata dict
        index: Index in the transitions list (0-based)
    """
    print(f"\n{'='*70}")
    print(f"Transition {index + 1}/{len(transitions)}")
    print(f"{'='*70}")

    # Song pair info
    song_a = transition['pair']['song_a']['filename']
    song_b = transition['pair']['song_b']['filename']
    section_a = transition['pair']['song_a']['sections_used'][0]['label']
    section_b = transition['pair']['song_b']['sections_used'][0]['label']

    print(f"\nFrom: {song_a} [{section_a}]")
    print(f"To:   {song_b} [{section_b}]")

    # Compatibility scores
    compat = transition['compatibility']
    print(f"\nCompatibility Score: {compat['overall_score']:.1f}/100")
    print(f"├─ Tempo:      {compat['components']['tempo']['score']:.1f}/100 "
          f"({compat['components']['tempo']['details']['tempo_a']:.1f} → "
          f"{compat['components']['tempo']['details']['tempo_b']:.1f} BPM, "
          f"diff: {compat['components']['tempo']['details']['diff_pct']:.1f}%)")
    print(f"├─ Key:       {compat['components']['key']['score']:.1f}/100 "
          f"({compat['components']['key']['details']['key_a']} → "
          f"{compat['components']['key']['details']['key_b']})")
    print(f"├─ Energy:     {compat['components']['energy']['score']:.1f}/100 "
          f"(diff: {compat['components']['energy']['details']['energy_diff_db']:.1f} dB)")
    print(f"└─ Embeddings: {compat['components']['embeddings']['score']:.1f}/100 "
          f"(stems: {compat['components']['embeddings']['details']['stems_used']})")

    # Variants available
    print(f"\nAvailable Variants:")
    for idx, variant in enumerate(transition['variants'], 1):
        variant_type = variant['variant_type']
        variant_type_upper = variant_type.upper()
        duration = variant['total_duration']
        size = variant['file_size_mb']

        # Description based on variant type
        if variant_type == 'medium-crossfade':
            desc = "Full sections with crossfade"
        elif variant_type == 'medium-silence':
            desc = f"Full sections with {variant.get('silence_beats', 4)}-beat silence gap"
        elif variant_type == 'vocal-fade':
            desc = f"Vocal-only bridge ({variant.get('transition_beats', 8)}-beat transition)"
        elif variant_type == 'drum-fade':
            desc = f"Drum-only bridge ({variant.get('transition_beats', 8)}-beat transition)"
        else:
            desc = "Unknown variant type"

        print(f"  [{idx}] {variant_type_upper:<18} ({duration:5.1f}s, {size:5.2f} MB) - {desc}")

    # Review status
    review = transition['review']
    if review['status'] != 'pending':
        print(f"\nCurrent Review Status: {review['status'].upper()}")
        if review.get('ratings', {}).get('overall'):
            print(f"  Overall Rating: {review['ratings']['overall']}/10")
        if review.get('preferred_variant'):
            print(f"  Preferred Variant: {review['preferred_variant']}")


def get_variant_path(transition, variant_type):
    """
    Get the full path to a variant audio file.

    Args:
        transition: Transition metadata dict
        variant_type: 'medium-crossfade', 'medium-silence', 'vocal-fade', or 'drum-fade'

    Returns:
        Path object or None if not found
    """
    variant = next((v for v in transition['variants'] if v['variant_type'] == variant_type), None)
    if not variant:
        return None

    # Filename is relative to output dir
    filepath = TRANSITIONS_DIR / variant['filename']

    if not filepath.exists():
        print(f"⚠️  WARNING: Audio file not found: {filepath}")
        return None

    return filepath


def collect_rating_input(transition):
    """
    Interactively collect ratings and feedback for a transition.

    Args:
        transition: Transition metadata dict

    Returns:
        Updated review dict, or None if cancelled
    """
    print(f"\n{'─'*70}")
    print(f"Rate Transition: {transition['pair']['song_a']['filename']} "
          f"[{transition['pair']['song_a']['sections_used'][0]['label']}] → "
          f"{transition['pair']['song_b']['filename']} "
          f"[{transition['pair']['song_b']['sections_used'][0]['label']}]")
    print(f"{'─'*70}")

    try:
        # Overall quality
        while True:
            overall = input("\nOverall Quality (1-10, or 'skip' to cancel): ").strip()
            if overall.lower() == 'skip':
                print("  Skipped rating")
                return None
            try:
                overall = int(overall)
                if 1 <= overall <= 10:
                    break
                print("  Please enter a number between 1 and 10")
            except ValueError:
                print("  Please enter a valid number")

        # Additional ratings
        ratings = {'overall': overall}

        rating_questions = [
            ('theme_fit', 'Theme Fit (1-10)'),
            ('musical_fit', 'Musical Fit (1-10)'),
            ('energy_flow', 'Energy Flow (1-10)'),
            ('lyrical_coherence', 'Lyrical Coherence (1-10)'),
            ('transition_smoothness', 'Transition Smoothness (1-10)')
        ]

        for key, prompt in rating_questions:
            while True:
                value = input(f"{prompt}: ").strip()
                if not value:
                    ratings[key] = None
                    break
                try:
                    value = int(value)
                    if 1 <= value <= 10:
                        ratings[key] = value
                        break
                    print("  Please enter a number between 1 and 10")
                except ValueError:
                    print("  Please enter a valid number or press Enter to skip")

        # Preferred variant
        print(f"\nPreferred Variant:")
        for idx, variant in enumerate(transition['variants'], 1):
            print(f"  [{idx}] {variant['variant_type']}")

        while True:
            pref = input("Choice (1-3, or Enter to skip): ").strip()
            if not pref:
                preferred_variant = None
                break
            try:
                pref = int(pref)
                if 1 <= pref <= len(transition['variants']):
                    preferred_variant = transition['variants'][pref - 1]['variant_type']
                    break
                print(f"  Please enter a number between 1 and {len(transition['variants'])}")
            except ValueError:
                print("  Please enter a valid number")

        # Recommended action
        print(f"\nRecommended Action:")
        print(f"  [1] Use in setlist")
        print(f"  [2] Needs refinement")
        print(f"  [3] Discard")

        while True:
            action = input("Choice (1-3, or Enter to skip): ").strip()
            if not action:
                recommended_action = None
                break
            try:
                action = int(action)
                if action == 1:
                    recommended_action = "use_in_setlist"
                    break
                elif action == 2:
                    recommended_action = "needs_refinement"
                    break
                elif action == 3:
                    recommended_action = "discard"
                    break
                else:
                    print("  Please enter 1, 2, or 3")
            except ValueError:
                print("  Please enter a valid number")

        # Additional notes
        notes = input("\nAdditional Notes (optional): ").strip()

        # Tags
        tags_input = input("Tags (comma-separated, optional): ").strip()
        tags = [t.strip() for t in tags_input.split(',') if t.strip()]

        # Build review object
        review = {
            'status': 'reviewed',
            'reviewed_at': datetime.now().isoformat(),
            'reviewer_notes': notes,
            'ratings': ratings,
            'preferred_variant': preferred_variant,
            'recommended_action': recommended_action,
            'tags': tags
        }

        print(f"\n✓ Ratings collected")
        return review

    except KeyboardInterrupt:
        print(f"\n\n  Rating cancelled")
        return None


# =============================================================================
# REVIEW PROGRESS TRACKING
# =============================================================================

def load_review_progress():
    """Load review progress from file."""
    if not PROGRESS_FILE.exists():
        return {
            'session_started': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'total_transitions': 0,
            'reviewed_count': 0,
            'current_index': 0,
            'review_sessions': []
        }

    try:
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        print(f"⚠️  Warning: Could not load review progress, starting fresh")
        return {
            'session_started': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'total_transitions': 0,
            'reviewed_count': 0,
            'current_index': 0,
            'review_sessions': []
        }


def save_review_progress(progress):
    """Save review progress to file."""
    try:
        PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROGRESS_FILE, 'w') as f:
            json.dump(progress, f, indent=2)
    except (IOError, OSError) as e:
        print(f"⚠️  Warning: Could not save review progress: {e}")


def export_summary_csv(index):
    """
    Export transitions summary to CSV (from index).

    Args:
        index: Transitions index dict
    """
    transitions = index['transitions']
    if not transitions:
        return

    summary_data = []
    for t in transitions:
        summary_data.append({
            'transition_id': t['transition_id'],
            'song_a': t['pair']['song_a']['filename'],
            'song_b': t['pair']['song_b']['filename'],
            'section_a': t['pair']['song_a']['sections_used'][0]['label'],
            'section_b': t['pair']['song_b']['sections_used'][0]['label'],
            'overall_score': t['compatibility']['overall_score'],
            'review_status': t['review']['status'],
            'review_rating': t['review']['ratings'].get('overall', None) if t['review']['ratings'] else None,
            'preferred_variant': t['review'].get('preferred_variant', None),
            'recommended_action': t['review'].get('recommended_action', None),
            'tags': ', '.join(t['review'].get('tags', []))
        })

    df = pd.DataFrame(summary_data)
    csv_path = METADATA_DIR / 'transitions_summary_reviewed.csv'
    df.to_csv(csv_path, index=False)
    print(f"\n✓ Exported review summary: {csv_path}")


# =============================================================================
# MAIN REVIEW LOOP
# =============================================================================

def show_help():
    """Display help message."""
    print(f"\n{'─'*70}")
    print("COMMANDS:")
    print(f"{'─'*70}")
    print("  p <variant>  - Play variant (e.g., 'p 1', 'p crossfade', 'p vocal', 'p drum')")
    print("  s            - Stop playback")
    print("  r            - Rate this transition")
    print("  n            - Next transition (without rating)")
    print("  b            - Previous transition")
    print("  j <number>   - Jump to specific transition (e.g., 'j 5')")
    print("  i            - Show transition info again")
    print("  q            - Quit and save progress")
    print("  h            - Help")
    print(f"{'─'*70}")


def main():
    """Main review interface loop."""
    global transitions

    print(f"\n{'='*70}")
    print(f"           Section Transition Review System v2.0")
    print(f"{'='*70}")

    # Load transitions index
    index = load_transitions_index()
    if not index:
        return 1

    transitions = index['transitions']
    if not transitions:
        print(f"\n❌ No transitions found in index")
        return 1

    # Load review progress
    progress = load_review_progress()
    if progress['total_transitions'] != len(transitions):
        # Reset if transition count changed
        progress['current_index'] = 0
        progress['total_transitions'] = len(transitions)

    current_index = progress['current_index']
    reviewed_in_session = 0
    session_start = datetime.now()
    quit_requested = False  # Flag to track intentional quit

    # Summary
    reviewed_count = sum(1 for t in transitions if t['review']['status'] != 'pending')
    print(f"\nLoaded {len(transitions)} transitions | Reviewed: {reviewed_count} | Pending: {len(transitions) - reviewed_count}")

    print(f"\nStarting at transition {current_index + 1}/{len(transitions)}")
    print(f"Type 'h' for help, 'q' to quit\n")

    try:
        while True:
            # Ensure current_index stays within bounds (wrap around)
            current_index = current_index % len(transitions)

            transition = transitions[current_index]

            # Display transition info
            display_transition_info(transition, current_index)

            # Command loop for this transition
            while True:
                try:
                    print(f"\n{'─'*70}")
                    command = input("> ").strip().lower()

                    if command == 'q':
                        # Quit - set flag and raise to exit both loops
                        quit_requested = True
                        raise KeyboardInterrupt

                    elif command == 'h':
                        # Help
                        show_help()

                    elif command == 'i':
                        # Show info again
                        display_transition_info(transition, current_index)

                    elif command.startswith('p'):
                        # Play variant
                        parts = command.split()
                        if len(parts) < 2:
                            print("  Usage: p <variant> (e.g., 'p 1', 'p short')")
                            continue

                        variant_spec = parts[1]

                        # Parse variant specification
                        if variant_spec.isdigit():
                            variant_idx = int(variant_spec) - 1
                            if 0 <= variant_idx < len(transition['variants']):
                                variant_type = transition['variants'][variant_idx]['variant_type']
                            else:
                                print(f"  Invalid variant number. Choose 1-{len(transition['variants'])}")
                                continue
                        elif variant_spec in ['medium-crossfade', 'medium-silence', 'vocal-fade', 'drum-fade',
                                              'crossfade', 'silence', 'vocal', 'drum']:
                            # Support both full names and short names
                            if variant_spec == 'crossfade':
                                variant_type = 'medium-crossfade'
                            elif variant_spec == 'silence':
                                variant_type = 'medium-silence'
                            elif variant_spec == 'vocal':
                                variant_type = 'vocal-fade'
                            elif variant_spec == 'drum':
                                variant_type = 'drum-fade'
                            else:
                                variant_type = variant_spec
                        else:
                            print(f"  Invalid variant. Use 1-4, 'crossfade', 'silence', 'vocal', 'drum', or full names")
                            continue

                        # Get file path and play
                        filepath = get_variant_path(transition, variant_type)
                        if filepath:
                            play_audio(filepath, blocking=True)

                    elif command == 's':
                        # Stop playback
                        stop_playback()

                    elif command == 'r':
                        # Rate transition
                        review = collect_rating_input(transition)
                        if review:
                            transition['review'] = review
                            save_transitions_index(index)
                            reviewed_in_session += 1
                            print(f"\n✓ Saved! ({reviewed_count + reviewed_in_session}/{len(transitions)} reviewed)")

                            # Auto-advance to next
                            current_index += 1
                            break

                    elif command == 'n':
                        # Next transition (without rating)
                        current_index += 1
                        break

                    elif command == 'b':
                        # Previous transition
                        current_index -= 1
                        break

                    elif command.startswith('j'):
                        # Jump to specific transition
                        parts = command.split()
                        if len(parts) < 2:
                            print(f"  Usage: j <number> (e.g., 'j 5' to jump to transition 5)")
                            continue

                        try:
                            target = int(parts[1])
                            if 1 <= target <= len(transitions):
                                current_index = target - 1
                                break
                            else:
                                print(f"  Invalid transition number. Choose 1-{len(transitions)}")
                        except ValueError:
                            print("  Please enter a valid number")

                    else:
                        print(f"  Unknown command: {command}. Type 'h' for help")

                except KeyboardInterrupt:
                    # Re-raise if this was an intentional quit
                    if quit_requested:
                        raise
                    # Otherwise, handle Ctrl+C during playback
                    stop_playback()
                    print()  # New line after ^C
                    continue

    except KeyboardInterrupt:
        print(f"\n\n{'='*70}")
        print("REVIEW SESSION ENDED")
        print(f"{'='*70}")

    # Save progress
    progress['current_index'] = current_index
    progress['last_updated'] = datetime.now().isoformat()
    progress['reviewed_count'] = reviewed_count + reviewed_in_session

    # Add session info
    session_info = {
        'session_id': len(progress['review_sessions']) + 1,
        'started': session_start.isoformat(),
        'ended': datetime.now().isoformat(),
        'transitions_reviewed': reviewed_in_session,
        'duration_minutes': round((datetime.now() - session_start).total_seconds() / 60, 1)
    }
    progress['review_sessions'].append(session_info)

    save_review_progress(progress)

    # Final summary
    print(f"\nSession Summary:")
    print(f"  Transitions reviewed this session: {reviewed_in_session}")
    print(f"  Total reviewed: {progress['reviewed_count']}/{len(transitions)}")
    print(f"  Duration: {session_info['duration_minutes']} minutes")

    # Export summary CSV
    export_summary_csv(index)

    print(f"\n✓ Progress saved. Run again to continue from transition {current_index + 1}")
    print(f"{'='*70}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
