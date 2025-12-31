import librosa
import numpy as np
import pathlib

def test_segmentation():
    # Create fake audio
    sr = 22050
    y = np.random.randn(sr * 10)
    
    chroma_seg = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=512)
    rec_matrix = librosa.segment.recurrence_matrix(
        chroma_seg, 
        mode='affinity',
        metric='cosine'
    )
    
    print(f"rec_matrix shape: {rec_matrix.shape}")
    
    # This is what's in the notebook
    try:
        novelty = librosa.segment.timelag_filter(rec_matrix)
        print(f"novelty shape: {novelty.shape}")
        
        peaks = librosa.util.peak_pick(
            novelty,
            pre_max=5, post_max=5,
            pre_avg=5, post_avg=5,
            delta=0.1, wait=10
        )
        print(f"peaks: {peaks}")
    except Exception as e:
        print(f"Caught error: {e}")

if __name__ == "__main__":
    test_segmentation()
