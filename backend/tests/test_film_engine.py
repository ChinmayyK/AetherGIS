import pytest
import numpy as np
from backend.app.services.interpolation import FILMEngine

def test_film_engine_interpolation():
    """Test that FILMEngine can generate intermediate frames."""
    engine = FILMEngine()
    
    # Mock two 256x256 RGB frames [0, 1]
    frame_a = np.random.rand(256, 256, 3).astype(np.float32)
    frame_b = np.random.rand(256, 256, 3).astype(np.float32)
    
    # If weights not present, engine will use LK fallback (which is also valid for this test)
    # But we want to test the FILM logic if possible.
    
    n = 2
    try:
        frames = engine.interpolate(frame_a, frame_b, n)
        
        assert len(frames) == n
        for f in frames:
            assert f.shape == (256, 256, 3)
            assert f.dtype == np.float32
            assert np.min(f) >= 0.0
            assert np.max(f) <= 1.0
            
    except Exception as e:
        pytest.fail(f"FILMEngine interpolation failed: {e}")

def test_film_engine_loading():
    """Test that engine attempts to load but handles missing weights gracefully."""
    engine = FILMEngine()
    assert engine.model_name == "film"
    # Even if not loaded, interpolate should work via fallback
    frame = np.zeros((64, 64, 3), dtype=np.float32)
    frames = engine.interpolate(frame, frame, 1)
    assert len(frames) == 1
