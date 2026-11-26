from voxcode.voice.commands import parse_command
from voxcode.voice.emotion.emotion_analyzer import EmotionAnalyzer


def test_voice_commands():
    assert parse_command("Accept the change.") == "accept"
    assert parse_command("Vox, run the tests") == "run_tests"
    assert parse_command("undo that") == "undo"
    assert parse_command("next file") == "next_file"
    assert parse_command("add a login route") is None


def test_emotion_matches_whole_words():
    analyzer = EmotionAnalyzer()
    assert analyzer.analyze("add quickstart docs").primary_emotion == "calm"
    assert analyzer.analyze("production down, hotfix asap").primary_emotion == "urgent"
    assert analyzer.analyze("this keeps breaking!").primary_emotion == "frustrated"
