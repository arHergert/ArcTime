"""
Kleine Regressionstest-Suite fuer die fehleranfaelligsten Stellen von ArcTime,
insbesondere die Titel-Matching-Logik (Ursache des "I Swear"-Bugs) und die
_norm_title-Namenskollision, die diese Session einmal real aufgetreten ist.

Ausfuehren mit:  pytest test_arctime.py -v
"""
import at_config
import at_metadata
import at_recommendations


# -- _norm_title (at_config) -------------------------------------------------

def test_norm_title_strips_all_non_alnum():
    assert at_config._norm_title("I, Swear!") == "iswear"
    assert at_config._norm_title("Yi Yi (2000)") == "yiyi2000"
    assert at_config._norm_title("") == ""
    assert at_config._norm_title(None) == ""


def test_norm_title_is_the_single_definition():
    # Regression: in dieser Session gab es zwischenzeitlich zwei gleichnamige
    # _norm_title-Definitionen im selben Modul, wobei die zweite die erste
    # lautlos ueberschrieben hat (Python loest globale Namen erst beim Aufruf
    # auf). Nach dem Split muss es GENAU eine Quelle geben: at_config.
    import inspect
    src = inspect.getsource(at_config._norm_title)
    assert "re.sub(r'[^a-z0-9]'" in src


# -- _norm_title_loose + _rank_tmdb_results (at_metadata) --------------------

def test_norm_title_loose_keeps_word_boundaries():
    assert at_metadata._norm_title_loose("I Swear") == "i swear"
    assert at_metadata._norm_title_loose("I, Swear!") == "i swear"
    assert at_metadata._norm_title_loose("ISwear") != at_metadata._norm_title_loose("I Swear")


def test_rank_tmdb_results_exact_match_wins_over_popularity():
    """Der eigentliche 'I Swear (2025)'-Bug: TMDB-Relevanz/Popularitaet allein
    haette 'I Swear to You: A Love Story' vor das exakt passende 'I Swear'
    gesetzt."""
    results = [
        {'title': 'I Swear to You: A Love Story', 'popularity': 50.0, 'genre_ids': []},
        {'title': 'I Swear', 'popularity': 1.2, 'genre_ids': []},
    ]
    ranked = at_metadata._rank_tmdb_results(results, 'movie', query='I Swear')
    assert ranked[0]['title'] == 'I Swear'


def test_rank_tmdb_results_prefers_prefix_over_contains():
    results = [
        {'title': 'The Great Ran Story', 'popularity': 10.0, 'genre_ids': []},
        {'title': 'Ran', 'popularity': 1.0, 'genre_ids': []},
    ]
    ranked = at_metadata._rank_tmdb_results(results, 'movie', query='Ran')
    assert ranked[0]['title'] == 'Ran'


def test_rank_tmdb_results_deprioritizes_documentaries():
    results = [
        {'title': 'Ran', 'popularity': 1.0, 'genre_ids': [99]},
        {'title': 'Ran', 'popularity': 1.0, 'genre_ids': []},
    ]
    ranked = at_metadata._rank_tmdb_results(results, 'movie', query='Ran')
    assert ranked[0]['genre_ids'] == []


def test_rank_tmdb_results_no_query_is_noop_order():
    results = [{'title': 'B'}, {'title': 'A'}]
    ranked = at_metadata._rank_tmdb_results(results, 'movie', query=None)
    assert [r['title'] for r in ranked] == ['B', 'A']


def test_rank_tmdb_results_empty_list():
    assert at_metadata._rank_tmdb_results([], 'movie', query='X') == []


# -- Key-Builder (_rec_key / _game_key / _dcov_key) --------------------------

def test_key_builders_use_shared_norm_title():
    rec_key = at_recommendations._rec_key('movie', 'I, Swear!', '2025')
    dcov_key = at_metadata._dcov_key('movie', 'I, Swear!', '2025')
    game_key = at_metadata._game_key('I, Swear!')
    assert rec_key == 'movie|iswear|2025'
    assert dcov_key == 'movie|iswear|2025'
    assert game_key == 'game|iswear'


def test_key_builders_are_case_and_punctuation_insensitive():
    a = at_recommendations._rec_key('movie', 'Yi Yi', '2000')
    b = at_recommendations._rec_key('movie', 'yi, yi!', '2000')
    assert a == b
