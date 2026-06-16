from epictrace.asr.worker import WorkerArgs, parse_args


def test_parse_args_basic():
    args = parse_args(["--session", "42", "--staging", "/tmp/42",
                       "--model", "medium", "--sources", "mic", "system_audio"])
    assert isinstance(args, WorkerArgs)
    assert args.session_id == 42
    assert args.staging_dir == "/tmp/42"
    assert args.model == "medium"
    assert args.sources == ["mic", "system_audio"]


def test_parse_args_default_model():
    args = parse_args(["--session", "1", "--staging", "/tmp/1", "--sources", "mic"])
    assert args.model == "large-v3"
    assert args.sources == ["mic"]
