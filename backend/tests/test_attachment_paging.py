from epictrace.agent.attachment_paging import read_attachment_slice


def test_first_slice_offsets_and_chunk():
    text = "0123456789abcdefghij"  # 20 chars
    slice_text, next_cursor, chunk, done = read_attachment_slice(
        reference_id=7, text=text, cursor=0, page_size=8
    )
    assert slice_text == "01234567"
    assert next_cursor == 8
    assert done is False
    assert chunk.text == "01234567"
    assert chunk.char_start == 0 and chunk.char_end == 8
    assert chunk.reference_id == 7
    assert chunk.source_kind == "attachment"
    assert chunk.source_type == "attachment"
    assert chunk.ingest_record_id == 0


def test_second_slice_continues_from_cursor():
    text = "0123456789abcdefghij"
    slice_text, next_cursor, chunk, done = read_attachment_slice(
        reference_id=7, text=text, cursor=8, page_size=8
    )
    assert slice_text == "89abcdef"
    assert next_cursor == 16
    assert chunk.char_start == 8 and chunk.char_end == 16
    assert done is False


def test_final_partial_slice_marks_done():
    text = "0123456789abcdefghij"
    slice_text, next_cursor, chunk, done = read_attachment_slice(
        reference_id=7, text=text, cursor=16, page_size=8
    )
    assert slice_text == "ghij"
    assert next_cursor == 20
    assert chunk.char_start == 16 and chunk.char_end == 20
    assert done is True


def test_cursor_at_or_past_end_is_empty_done():
    text = "abc"
    slice_text, next_cursor, chunk, done = read_attachment_slice(
        reference_id=1, text=text, cursor=3, page_size=8
    )
    assert slice_text == ""
    assert next_cursor == 3
    assert chunk is None
    assert done is True


def test_empty_text_is_empty_done():
    slice_text, next_cursor, chunk, done = read_attachment_slice(
        reference_id=1, text="", cursor=0, page_size=8
    )
    assert slice_text == "" and next_cursor == 0 and chunk is None and done is True
