#!/usr/bin/env python3
"""
Test script to verify all-in-one and dependencies can be imported correctly.
"""

import sys
print(f"Python version: {sys.version}")
print(f"Python executable: {sys.executable}\n")

print("=" * 70)
print("DEPENDENCY TEST: All-In-One Library")
print("=" * 70)

# Test 1: PyTorch
print("\n[1/5] Testing PyTorch...")
try:
    import torch
    print(f"✓ PyTorch imported successfully")
    print(f"  Version: {torch.__version__}")
    print(f"  CUDA available: {torch.cuda.is_available()}")
except ImportError as e:
    print(f"✗ PyTorch import failed: {e}")
    sys.exit(1)

# Test 2: Torchaudio
print("\n[2/5] Testing Torchaudio...")
try:
    import torchaudio
    print(f"✓ Torchaudio imported successfully")
    print(f"  Version: {torchaudio.__version__}")
except ImportError as e:
    print(f"✗ Torchaudio import failed: {e}")
    sys.exit(1)

# Test 3: NATTEN
print("\n[3/5] Testing NATTEN...")
try:
    import natten
    print(f"✓ NATTEN imported successfully")
    print(f"  Version: {natten.__version__}")
    print(f"  Location: {natten.__file__}")

    # Try to import the specific function that's failing
    try:
        from natten.functional import na1d_qk
        print(f"✓ NATTEN na1d_qk imported successfully")
    except ImportError as e:
        print(f"✗ NATTEN na1d_qk import failed: {e}")
        print(f"  This is the error preventing all-in-one from working")

        # Show what's available in natten.functional
        print(f"\n  Available in natten.functional:")
        import natten.functional as nf
        attrs = [attr for attr in dir(nf) if not attr.startswith('_')]
        for attr in attrs[:10]:  # Show first 10
            print(f"    - {attr}")
        if len(attrs) > 10:
            print(f"    ... and {len(attrs) - 10} more")

except ImportError as e:
    print(f"✗ NATTEN import failed: {e}")
    print(f"  All-in-one requires NATTEN to be installed")
    sys.exit(1)

# Test 4: All-in-one
print("\n[4/5] Testing All-In-One...")
try:
    import allin1
    print(f"✓ All-in-one imported successfully")
    print(f"  Location: {allin1.__file__}")

    # Check if analyze function exists
    if hasattr(allin1, 'analyze'):
        print(f"✓ allin1.analyze() function available")
    else:
        print(f"✗ allin1.analyze() function not found")

except ImportError as e:
    print(f"✗ All-in-one import failed: {e}")
    sys.exit(1)

# Test 5: Simple analysis test (if a test file exists)
print("\n[5/5] Testing All-In-One analyze function...")
try:
    from pathlib import Path
    test_audio = Path("poc_audio/praise.mp3")

    if test_audio.exists():
        print(f"✓ Test audio file found: {test_audio}")
        print(f"  Attempting analysis (this may take a minute)...")

        result = allin1.analyze(
            str(test_audio),
            out_dir=None,
            visualize=False,
            include_embeddings=False,
            sonify=False
        )

        print(f"✓ Analysis completed successfully!")
        print(f"  BPM: {result.bpm}")
        print(f"  Beats detected: {len(result.beats)}")
        print(f"  Downbeats detected: {len(result.downbeats)}")
        print(f"  Segments detected: {len(result.segments)}")

    else:
        print(f"⚠ Test audio file not found: {test_audio}")
        print(f"  Skipping analysis test")

except Exception as e:
    print(f"✗ Analysis test failed: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("DEPENDENCY TEST COMPLETED")
print("=" * 70)