-- Local demo only. Transcript excerpts have mock speakers and embeddings.
BEGIN;

INSERT INTO meetings (id, source_path, filename, meeting_date, meeting_type,
    status, transcription_model, diarization_model, text_embedding_model,
    voice_embedding_model, text_embedding_dimensions, voice_embedding_dimensions)
VALUES ('7de00000-0000-4000-8000-000000000001', 'fixture:commitment-demo/vimeo-1154082574.mp4', '2026-01-14 Committee on Finance.mp4',
    '2026-01-14', 'Committee on Finance', 'completed', 'reference-autocaptions',
    'fixture-placeholder', 'fixture-placeholder', 'fixture-placeholder', 3, 3)
ON CONFLICT (id) DO NOTHING;

INSERT INTO published_sources (key, content_sha256, meeting_id)
VALUES ('commitment-demo/vimeo-1154082574.mp4', '718b075cf9c1c43b9861277f04fda9a3e29e77cb39a130eb31a2b7f9b87a2ad9', '7de00000-0000-4000-8000-000000000001')
ON CONFLICT (key) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000100', '7de00000-0000-4000-8000-000000000001', 'excerpt-1')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000100', '7de00000-0000-4000-8000-000000000001', '7de00000-0000-4000-8001-000000000100', 0,
    11066.005, 11088.325, 'Uh, moving on to item number eight, which is a substitute ordinance authorizing submission of the United States Department of Housing and Urban Development, HUD 1 0 8 loan application for the rehabilitation of 2135 North Milwaukee Avenue, better known as the Congress Theater In the first ward.', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000101', '7de00000-0000-4000-8000-000000000001', 'excerpt-2')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000101', '7de00000-0000-4000-8000-000000000001', '7de00000-0000-4000-8001-000000000101', 1,
    11967.195, 11993.305, 'Uh, in, in the event of a, uh, default and yes, foreclosure. So the, The more eloquent way of saying it, The, the city will be guaranteeing these funds through future CBDG payments. However, the senior loan will provide a risk mitigation, um, in the potential of result of a foreclosure to recapture any funds from the sale of the property.', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000102', '7de00000-0000-4000-8000-000000000001', 'excerpt-3')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000102', '7de00000-0000-4000-8000-000000000001', '7de00000-0000-4000-8001-000000000102', 2,
    12082.775, 12093.415, 'If they''re seeing none, alderman Irvin moves to pass. All those in favor signify by saying Aye. Aye. Aye. Opposed? In the opinion of the chair, the ayes have it,', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000103', '7de00000-0000-4000-8000-000000000001', 'excerpt-4')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000103', '7de00000-0000-4000-8000-000000000001', '7de00000-0000-4000-8001-000000000103', 3,
    12093.415, 12100.485, 'and the substitute ordinance will be reported out of the do pass recommendation will report it out at the next city Council meeting.', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

INSERT INTO meetings (id, source_path, filename, meeting_date, meeting_type,
    status, transcription_model, diarization_model, text_embedding_model,
    voice_embedding_model, text_embedding_dimensions, voice_embedding_dimensions)
VALUES ('7de00000-0000-4000-8000-000000000002', 'fixture:commitment-demo/vimeo-1156547113.mp4', '2026-01-21 City Council.mp4',
    '2026-01-21', 'City Council', 'completed', 'reference-autocaptions',
    'fixture-placeholder', 'fixture-placeholder', 'fixture-placeholder', 3, 3)
ON CONFLICT (id) DO NOTHING;

INSERT INTO published_sources (key, content_sha256, meeting_id)
VALUES ('commitment-demo/vimeo-1156547113.mp4', '86284bbf6dbab68961a48406196038c27635cf4a8418af6f13637858f6aaa6b1', '7de00000-0000-4000-8000-000000000002')
ON CONFLICT (key) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000200', '7de00000-0000-4000-8000-000000000002', 'excerpt-1')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000200', '7de00000-0000-4000-8000-000000000002', '7de00000-0000-4000-8001-000000000200', 0,
    5616.375, 5638.705, 'Item number three is the substitute ordinance authorizing the submission of the US Department of Housing and Urban Development Section 1 0 8 loan application for the rehabilitation of 2135 North Milwaukee Avenue, better known as the Congress Theater in the first ward in an amount up to $25.25 million.', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000201', '7de00000-0000-4000-8000-000000000002', 'excerpt-2')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000201', '7de00000-0000-4000-8000-000000000002', '7de00000-0000-4000-8001-000000000201', 1,
    5638.705, 5640.905, 'I move passage of this item by the same motion', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

INSERT INTO speakers (id, meeting_id, label)
VALUES ('7de00000-0000-4000-8001-000000000202', '7de00000-0000-4000-8000-000000000002', 'excerpt-3')
ON CONFLICT (id) DO NOTHING;
INSERT INTO transcript_segments (id, meeting_id, speaker_id, sequence,
    start_seconds, end_seconds, text, text_embedding, voice_embedding)
VALUES ('7de00000-0000-4000-8002-000000000202', '7de00000-0000-4000-8000-000000000002', '7de00000-0000-4000-8001-000000000202', 2,
    5640.905, 5642.815, 'Hearing. No objection. So ordered', '[1,0,0]', '[1,0,0]')
ON CONFLICT (id) DO NOTHING;

COMMIT;
