#!/usr/bin/env python3
"""
Transition Feedback Analysis: Worship Music Transition System

Version: 2.0.0
Date: 2026-01-05
Purpose: Analyze correlation between compatibility scores and human ratings

Features:
- Correlation analysis between computed scores and human ratings
- Identify which score components best predict human preference
- Generate weight tuning recommendations
- Export insights for setlist building
"""

import warnings
warnings.filterwarnings('ignore')

# Data analysis
import numpy as np
import pandas as pd
from scipy import stats
import json
from pathlib import Path
import matplotlib.pyplot as plt
import seaborn as sns
sns.set_style("whitegrid")

# Configuration
OUTPUT_DIR = Path("poc_output_allinone")
TRANSITIONS_DIR = OUTPUT_DIR / "section_transitions"
METADATA_DIR = TRANSITIONS_DIR / "metadata"
INDEX_FILE = METADATA_DIR / "transitions_index.json"
ANALYSIS_OUTPUT_DIR = METADATA_DIR / "analysis"


# =============================================================================
# DATA LOADING
# =============================================================================

def load_reviewed_transitions():
    """
    Load transitions index and filter for reviewed transitions.

    Returns:
        (index, reviewed_transitions) tuple, or (None, None) if error
    """
    if not INDEX_FILE.exists():
        print(f"❌ ERROR: Transitions index not found at: {INDEX_FILE}")
        return None, None

    try:
        with open(INDEX_FILE, 'r') as f:
            index = json.load(f)

        # Filter for reviewed transitions
        reviewed = [
            t for t in index['transitions']
            if t['review']['status'] in ['reviewed', 'approved', 'rejected']
            and t['review']['ratings'].get('overall') is not None
        ]

        print(f"✓ Loaded transitions index")
        print(f"  Total transitions: {len(index['transitions'])}")
        print(f"  Reviewed: {len(reviewed)}")

        if len(reviewed) < 5:
            print(f"\n⚠️  WARNING: Only {len(reviewed)} reviewed transitions found")
            print(f"   Need at least 5-10 reviews for meaningful analysis")

        return index, reviewed

    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ ERROR: Failed to load transitions index: {e}")
        return None, None


# =============================================================================
# CORRELATION ANALYSIS
# =============================================================================

def analyze_score_correlations(reviewed_transitions):
    """
    Analyze correlation between compatibility scores and human ratings.

    Args:
        reviewed_transitions: List of reviewed transition dicts

    Returns:
        DataFrame with correlation analysis results
    """
    print(f"\n{'='*70}")
    print("CORRELATION ANALYSIS")
    print(f"{'='*70}")

    # Extract data
    data = []
    for t in reviewed_transitions:
        compat = t['compatibility']
        review = t['review']

        row = {
            'transition_id': t['transition_id'][:8],  # Short ID
            'overall_score': compat['overall_score'],
            'tempo_score': compat['components']['tempo']['score'],
            'key_score': compat['components']['key']['score'],
            'energy_score': compat['components']['energy']['score'],
            'embeddings_score': compat['components']['embeddings']['score'],
            'human_overall': review['ratings'].get('overall'),
            'human_theme_fit': review['ratings'].get('theme_fit'),
            'human_musical_fit': review['ratings'].get('musical_fit'),
            'human_energy_flow': review['ratings'].get('energy_flow'),
            'human_lyrical_coherence': review['ratings'].get('lyrical_coherence'),
            'human_transition_smoothness': review['ratings'].get('transition_smoothness'),
            'recommended_action': review.get('recommended_action'),
            'preferred_variant': review.get('preferred_variant')
        }
        data.append(row)

    df = pd.DataFrame(data)

    # Calculate correlations between computed scores and human ratings
    score_cols = ['overall_score', 'tempo_score', 'key_score', 'energy_score', 'embeddings_score']
    human_cols = [col for col in df.columns if col.startswith('human_') and df[col].notna().sum() > 0]

    print(f"\nCorrelation between computed scores and human ratings:")
    print(f"{'─'*70}")

    correlations = []
    for score_col in score_cols:
        for human_col in human_cols:
            # Filter out NaN values
            valid_data = df[[score_col, human_col]].dropna()

            if len(valid_data) < 3:
                continue

            # Pearson correlation
            pearson_r, pearson_p = stats.pearsonr(valid_data[score_col], valid_data[human_col])

            # Spearman rank correlation (more robust to outliers)
            spearman_r, spearman_p = stats.spearmanr(valid_data[score_col], valid_data[human_col])

            correlations.append({
                'computed_score': score_col,
                'human_rating': human_col.replace('human_', ''),
                'pearson_r': pearson_r,
                'pearson_p': pearson_p,
                'spearman_r': spearman_r,
                'spearman_p': spearman_p,
                'n_samples': len(valid_data)
            })

    corr_df = pd.DataFrame(correlations)
    corr_df = corr_df.sort_values('pearson_r', ascending=False)

    # Display top correlations
    print("\nTop Correlations (Pearson r):")
    print(corr_df[['computed_score', 'human_rating', 'pearson_r', 'pearson_p', 'n_samples']].head(10).to_string(index=False))

    return df, corr_df


# =============================================================================
# WEIGHT OPTIMIZATION
# =============================================================================

def recommend_weight_adjustments(corr_df, current_weights):
    """
    Recommend weight adjustments based on correlation analysis.

    Args:
        corr_df: Correlation analysis DataFrame
        current_weights: Current weight configuration dict

    Returns:
        Dict with recommended weights
    """
    print(f"\n{'='*70}")
    print("WEIGHT TUNING RECOMMENDATIONS")
    print(f"{'='*70}")

    # Calculate average correlation for each computed score vs human_overall
    score_to_component = {
        'overall_score': None,  # Skip overall
        'tempo_score': 'tempo',
        'key_score': 'key',
        'energy_score': 'energy',
        'embeddings_score': 'embeddings'
    }

    # Get correlations with human overall rating
    overall_corrs = corr_df[corr_df['human_rating'] == 'overall'].copy()

    if overall_corrs.empty:
        print("\n⚠️  No correlations with human overall ratings found")
        print("   Cannot generate weight recommendations")
        return None

    # Calculate relative importance
    component_importance = {}
    for _, row in overall_corrs.iterrows():
        component = score_to_component.get(row['computed_score'])
        if component and row['pearson_p'] < 0.1:  # Significant at p < 0.1
            # Use absolute correlation (direction doesn't matter for weighting)
            component_importance[component] = abs(row['pearson_r'])

    if not component_importance:
        print("\n⚠️  No significant correlations found (p < 0.1)")
        print("   Current weights:")
        for comp, weight in current_weights.items():
            print(f"     {comp}: {weight:.2f}")
        return current_weights

    # Normalize to sum to 1.0
    total_importance = sum(component_importance.values())
    recommended_weights = {
        comp: importance / total_importance
        for comp, importance in component_importance.items()
    }

    # Fill in missing components with small default values
    for comp in ['tempo', 'key', 'energy', 'embeddings']:
        if comp not in recommended_weights:
            recommended_weights[comp] = 0.05

    # Renormalize
    total = sum(recommended_weights.values())
    recommended_weights = {comp: weight / total for comp, weight in recommended_weights.items()}

    # Display comparison
    print(f"\nCurrent weights:")
    for comp in ['tempo', 'key', 'energy', 'embeddings']:
        curr = current_weights.get(comp, 0.0)
        print(f"  {comp:12s}: {curr:.3f}")

    print(f"\nRecommended weights (based on correlation with human ratings):")
    for comp in ['tempo', 'key', 'energy', 'embeddings']:
        rec = recommended_weights.get(comp, 0.0)
        curr = current_weights.get(comp, 0.0)
        change = rec - curr
        symbol = '↑' if change > 0.05 else '↓' if change < -0.05 else '≈'
        print(f"  {comp:12s}: {rec:.3f}  ({symbol} {change:+.3f})")

    print(f"\nInterpretation:")
    print(f"  Components with higher correlation to human ratings should receive more weight")
    print(f"  ↑ = Increase recommended, ↓ = Decrease recommended, ≈ = No major change")

    return recommended_weights


# =============================================================================
# VARIANT PREFERENCE ANALYSIS
# =============================================================================

def analyze_variant_preferences(reviewed_transitions):
    """
    Analyze which variants (short/medium/long) users prefer.

    Args:
        reviewed_transitions: List of reviewed transition dicts

    Returns:
        DataFrame with variant preference statistics
    """
    print(f"\n{'='*70}")
    print("VARIANT PREFERENCE ANALYSIS")
    print(f"{'='*70}")

    # Count variant preferences
    preferences = [
        t['review'].get('preferred_variant')
        for t in reviewed_transitions
        if t['review'].get('preferred_variant')
    ]

    if not preferences:
        print("\n⚠️  No variant preferences recorded")
        return None

    # Count by type
    variant_counts = pd.Series(preferences).value_counts()

    print(f"\nPreferred Variants:")
    for variant, count in variant_counts.items():
        percentage = count / len(preferences) * 100
        print(f"  {variant:8s}: {count:3d} ({percentage:5.1f}%)")

    # Analyze by compatibility score ranges
    data = []
    for t in reviewed_transitions:
        if not t['review'].get('preferred_variant'):
            continue

        data.append({
            'overall_score': t['compatibility']['overall_score'],
            'preferred_variant': t['review']['preferred_variant'],
            'human_overall': t['review']['ratings'].get('overall')
        })

    df = pd.DataFrame(data)

    if not df.empty:
        print(f"\nPreferred Variant by Compatibility Score:")
        # Bin scores into ranges
        df['score_range'] = pd.cut(df['overall_score'], bins=[0, 60, 75, 85, 100],
                                    labels=['60-75', '75-85', '85-100', '100+'])

        pref_by_score = df.groupby(['score_range', 'preferred_variant']).size().unstack(fill_value=0)
        print(pref_by_score)

    return df


# =============================================================================
# SETLIST INSIGHTS
# =============================================================================

def generate_setlist_insights(reviewed_transitions):
    """
    Generate insights for setlist building based on review data.

    Args:
        reviewed_transitions: List of reviewed transition dicts

    Returns:
        Dict with setlist recommendations
    """
    print(f"\n{'='*70}")
    print("SETLIST BUILDING INSIGHTS")
    print(f"{'='*70}")

    # Filter approved transitions
    approved = [
        t for t in reviewed_transitions
        if t['review'].get('recommended_action') == 'use_in_setlist'
    ]

    needs_refinement = [
        t for t in reviewed_transitions
        if t['review'].get('recommended_action') == 'needs_refinement'
    ]

    discard = [
        t for t in reviewed_transitions
        if t['review'].get('recommended_action') == 'discard'
    ]

    print(f"\nTransition Quality Breakdown:")
    print(f"  Approved for setlists:  {len(approved):3d}")
    print(f"  Needs refinement:       {len(needs_refinement):3d}")
    print(f"  Discard:                {len(discard):3d}")

    if approved:
        print(f"\n✓ Top Approved Transitions:")
        for idx, t in enumerate(sorted(approved,
                                        key=lambda x: x['review']['ratings'].get('overall', 0),
                                        reverse=True)[:5], 1):
            song_a = t['pair']['song_a']['filename']
            song_b = t['pair']['song_b']['filename']
            section_a = t['pair']['song_a']['sections_used'][0]['label']
            section_b = t['pair']['song_b']['sections_used'][0]['label']
            rating = t['review']['ratings'].get('overall', 0)
            score = t['compatibility']['overall_score']

            print(f"  {idx}. {song_a} [{section_a}] → {song_b} [{section_b}]")
            print(f"     Human: {rating}/10 | Computed: {score:.1f}/100")

    # Tag analysis
    all_tags = []
    for t in reviewed_transitions:
        all_tags.extend(t['review'].get('tags', []))

    if all_tags:
        tag_counts = pd.Series(all_tags).value_counts()
        print(f"\n📌 Most Common Tags:")
        for tag, count in tag_counts.head(10).items():
            print(f"  {tag:25s}: {count:3d}")

    insights = {
        'total_reviewed': len(reviewed_transitions),
        'approved_count': len(approved),
        'needs_refinement_count': len(needs_refinement),
        'discard_count': len(discard),
        'top_tags': tag_counts.head(10).to_dict() if all_tags else {}
    }

    return insights


# =============================================================================
# VISUALIZATION
# =============================================================================

def create_analysis_visualizations(df, corr_df, reviewed_transitions):
    """
    Create visualization plots for analysis results.

    Args:
        df: Main data DataFrame
        corr_df: Correlation analysis DataFrame
        reviewed_transitions: List of reviewed transitions
    """
    print(f"\n{'='*70}")
    print("GENERATING VISUALIZATIONS")
    print(f"{'='*70}")

    ANALYSIS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Figure 1: Correlation heatmap
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # Computed scores vs human overall
    ax = axes[0]
    score_cols = ['overall_score', 'tempo_score', 'key_score', 'energy_score', 'embeddings_score']
    scores_df = df[score_cols + ['human_overall']].dropna()

    if not scores_df.empty:
        corr_matrix = scores_df.corr()
        sns.heatmap(corr_matrix, annot=True, fmt='.2f', cmap='coolwarm', center=0,
                    vmin=-1, vmax=1, ax=ax, cbar_kws={'label': 'Correlation'})
        ax.set_title('Correlation: Computed Scores vs Human Ratings', fontsize=14, fontweight='bold')

    # Scatter plot: Overall score vs Human overall
    ax = axes[1]
    valid_data = df[['overall_score', 'human_overall']].dropna()

    if not valid_data.empty:
        ax.scatter(valid_data['overall_score'], valid_data['human_overall'],
                   alpha=0.6, s=100, edgecolors='black', linewidth=0.5)

        # Add trend line
        z = np.polyfit(valid_data['overall_score'], valid_data['human_overall'], 1)
        p = np.poly1d(z)
        ax.plot(valid_data['overall_score'], p(valid_data['overall_score']),
                "r--", alpha=0.8, linewidth=2, label=f'Trend: y={z[0]:.3f}x+{z[1]:.1f}')

        # Correlation coefficient
        r, p_val = stats.pearsonr(valid_data['overall_score'], valid_data['human_overall'])
        ax.text(0.05, 0.95, f'r = {r:.3f}\np = {p_val:.4f}',
                transform=ax.transAxes, fontsize=12, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        ax.set_xlabel('Computed Overall Score', fontsize=12)
        ax.set_ylabel('Human Overall Rating (1-10)', fontsize=12)
        ax.set_title('Computed Score vs Human Rating', fontsize=14, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    viz_path = ANALYSIS_OUTPUT_DIR / 'correlation_analysis.png'
    plt.savefig(viz_path, dpi=150, bbox_inches='tight')
    print(f"  ✓ Saved: {viz_path}")
    plt.close()

    # Figure 2: Variant preferences
    preferences = [t['review'].get('preferred_variant') for t in reviewed_transitions
                   if t['review'].get('preferred_variant')]

    if preferences:
        fig, ax = plt.subplots(figsize=(10, 6))
        variant_counts = pd.Series(preferences).value_counts()
        variant_counts.plot(kind='bar', ax=ax, color=['#FF6B6B', '#4ECDC4', '#45B7D1'])

        ax.set_title('Preferred Transition Variants', fontsize=14, fontweight='bold')
        ax.set_xlabel('Variant Type', fontsize=12)
        ax.set_ylabel('Count', fontsize=12)
        ax.set_xticklabels(ax.get_xticklabels(), rotation=0)
        ax.grid(True, alpha=0.3, axis='y')

        # Add percentage labels
        total = sum(variant_counts)
        for i, v in enumerate(variant_counts):
            ax.text(i, v + 0.5, f'{v/total*100:.1f}%', ha='center', fontsize=11)

        plt.tight_layout()
        viz_path = ANALYSIS_OUTPUT_DIR / 'variant_preferences.png'
        plt.savefig(viz_path, dpi=150, bbox_inches='tight')
        print(f"  ✓ Saved: {viz_path}")
        plt.close()


# =============================================================================
# EXPORT RESULTS
# =============================================================================

def export_analysis_results(insights, corr_df, recommended_weights, current_weights):
    """
    Export analysis results to JSON file.

    Args:
        insights: Setlist insights dict
        corr_df: Correlation analysis DataFrame
        recommended_weights: Recommended weights dict
        current_weights: Current weights dict
    """
    ANALYSIS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        'generated_at': datetime.now().isoformat(),
        'insights': insights,
        'correlations': corr_df.to_dict('records') if corr_df is not None else [],
        'weight_recommendations': {
            'current': current_weights,
            'recommended': recommended_weights if recommended_weights else current_weights
        }
    }

    output_path = ANALYSIS_OUTPUT_DIR / 'feedback_analysis.json'
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n  ✓ Analysis results exported: {output_path}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main analysis execution."""
    from datetime import datetime

    print(f"\n{'='*70}")
    print("Transition Feedback Analysis")
    print(f"{'='*70}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Load data
    index, reviewed_transitions = load_reviewed_transitions()
    if not reviewed_transitions:
        print(f"\n❌ No reviewed transitions found")
        print(f"   Please run review_transitions.py to review transitions first")
        return 1

    # Get current weights from index configuration
    current_weights = index['configuration'].get('weights', {
        'tempo': 0.25,
        'key': 0.25,
        'energy': 0.15,
        'embeddings': 0.35
    })

    # Run analyses
    df, corr_df = analyze_score_correlations(reviewed_transitions)
    recommended_weights = recommend_weight_adjustments(corr_df, current_weights)
    variant_df = analyze_variant_preferences(reviewed_transitions)
    insights = generate_setlist_insights(reviewed_transitions)

    # Create visualizations
    if len(reviewed_transitions) >= 5:
        create_analysis_visualizations(df, corr_df, reviewed_transitions)
    else:
        print(f"\n⚠️  Skipping visualizations (need at least 5 reviews, have {len(reviewed_transitions)})")

    # Export results
    export_analysis_results(insights, corr_df, recommended_weights, current_weights)

    print(f"\n{'='*70}")
    print("ANALYSIS COMPLETE")
    print(f"{'='*70}")
    print(f"\nOutputs saved to: {ANALYSIS_OUTPUT_DIR.absolute()}")
    print(f"  - feedback_analysis.json: Complete analysis results")
    print(f"  - correlation_analysis.png: Score vs rating correlations")
    print(f"  - variant_preferences.png: Preferred variant distribution")
    print(f"\nCompleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
