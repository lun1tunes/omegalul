-- Register Schedule Builder after the Python service exists.

INSERT INTO agent_registry (agent_id, title, when_to_use, input_required, output_provides)
VALUES (
    'schedule_builder',
    'Schedule Builder',
    'Исходный SCHEDULE (.inc): сдвиг дат ввода по фактам Excel (скважина+дата); перепривязка скважин в группу (GRUPTREE/GCONPROD) по тексту задачи и baseline. Excel обязателен только для новых дат ввода. Не выдумывает даты и имена, которых нет в задаче, Excel или baseline.',
    '["schedule_source"]'::jsonb,
    '["schedule_out", "diff"]'::jsonb
)
ON CONFLICT (agent_id) DO UPDATE SET
    title = EXCLUDED.title,
    when_to_use = EXCLUDED.when_to_use,
    input_required = EXCLUDED.input_required,
    output_provides = EXCLUDED.output_provides;
