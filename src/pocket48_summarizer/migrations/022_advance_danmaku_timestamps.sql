UPDATE danmaku_entries
SET timestamp_ms = MAX(0, timestamp_ms - 3000);

UPDATE danmaku_peaks
SET start_ms = MAX(0, start_ms - 3000),
    end_ms = MAX(0, end_ms - 3000),
    samples_json = (
        SELECT json(
            COALESCE(
                json_group_array(
                    json_set(
                        sample.value,
                        '$.timestamp_ms',
                        MAX(
                            0,
                            CAST(
                                json_extract(
                                    sample.value,
                                    '$.timestamp_ms'
                                ) AS INTEGER
                            ) - 3000
                        )
                    )
                ),
                '[]'
            )
        )
        FROM json_each(danmaku_peaks.samples_json) AS sample
    );

UPDATE jobs
SET summary_json = json_set(
    summary_json,
    '$.danmaku_peak_summaries',
    (
        SELECT json(
            COALESCE(
                json_group_array(
                    json_set(
                        json_set(
                            item.value,
                            '$.start_ms',
                            MAX(
                                0,
                                CAST(
                                    json_extract(
                                        item.value,
                                        '$.start_ms'
                                    ) AS INTEGER
                                ) - 3000
                            )
                        ),
                        '$.end_ms',
                        MAX(
                            0,
                            CAST(
                                json_extract(
                                    item.value,
                                    '$.end_ms'
                                ) AS INTEGER
                            ) - 3000
                        )
                    )
                ),
                '[]'
            )
        )
        FROM json_each(
            jobs.summary_json,
            '$.danmaku_peak_summaries'
        ) AS item
    )
)
WHERE summary_json IS NOT NULL
  AND json_valid(summary_json)
  AND json_type(
      summary_json,
      '$.danmaku_peak_summaries'
  ) = 'array';
