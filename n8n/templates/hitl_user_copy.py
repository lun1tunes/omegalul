"""Human-ask compiler for HITL gates.

Machine findings keep {code, slots}. This fragment turns them into Russian
user_message + questions[].text. Inject into Code nodes that emit HITL.
"""

from __future__ import annotations

HITL_USER_COPY_JS = r"""
const HITL_CYRILLIC=/[А-Яа-яЁё]/;
const HITL_CODE_ONLY=/^[A-Z][A-Z0-9_]{3,}$/;
const hitlClean=v=>typeof v==='string'?v.trim():'';
const hitlLooksMachine=text=>{
  const t=hitlClean(text);
  if(!t) return true;
  if(HITL_CODE_ONLY.test(t)) return true;
  if(!HITL_CYRILLIC.test(t)) return true;
  return false;
};
const hitlBaseName=p=>{
  const s=hitlClean(p).replace(/\\/g,'/');
  if(!s) return '';
  const parts=s.split('/').filter(Boolean);
  return parts[parts.length-1]||s;
};
const hitlSlot=(f,keys)=>{
  if(!f||typeof f!=='object') return '';
  for(const k of keys){
    const v=hitlClean(f[k]);
    if(v) return v;
  }
  return '';
};
const hitlSlots=f=>{
  const rawPath=hitlSlot(f,['path','target_file_ref','raw_path','include_path']);
  const rawFrom=hitlSlot(f,['file_ref','source_file_ref','root_path']);
  const path=hitlBaseName(rawPath)||(!rawPath?hitlBaseName(rawFrom):'');
  const from=rawPath?hitlBaseName(rawFrom):'';
  const keywords=Array.isArray(f?.keywords)?f.keywords.map(v=>hitlClean(v).toUpperCase()).filter(Boolean).slice(0,12):[];
  const cycle=Array.isArray(f?.cycle)?f.cycle.map(hitlBaseName).filter(Boolean):[];
  const wells=Array.isArray(f?.wells)?f.wells.map(hitlClean).filter(Boolean).slice(0,12):[];
  return {
    code:hitlClean(f?.code||f?.id||''),
    path, from,
    keyword:hitlClean(f?.keyword||'').toUpperCase(),
    entity:hitlClean(f?.entity||f?.well||''),
    field:hitlClean(f?.field||''),
    keywords, cycle, wells,
  };
};
const HITL_STAGE={
  schedule_intake_result:'Проверка входных данных задачи',
  baseline_analysis:'Проверка пакета schedule (INCLUDE и корневой файл)',
  baseline_decode_result:'Разбор записей baseline',
  schedule_validation_result:'Проверка синтаксиса, дат и семантики',
  schedule_render_result:'Сборка текста по схеме keyword',
  schedule_merge_result:'Сборка итогового schedule',
  schedule_commissioning_revise_result:'Сдвиг дат ввода (commissioning)',
  schedule_group_rebind_revise_result:'Перепривязка групп скважин',
  schedule_builder_stage_result:'Проверка черновика Builder',
  schedule_verifier_result:'Независимая проверка выпуска',
  baseline_inventory_query_result:'Выборка записей baseline',
};
const hitlDedupeKey=f=>{
  const s=hitlSlots(f);
  return [s.code,s.path,s.from,s.keyword,s.entity,s.field].join('|');
};
const HITL_GENERIC_CLUSTER='Проверка остановила задачу: не хватает исходных данных. Прикрепите недостающие файлы или напишите, как продолжать, и нажмите Ответить.';
const HITL_GENERIC_ITEM='Нужны исходные данные. Прикрепите недостающие файлы или напишите, как продолжать.';
const HITL_COPY={
  INCLUDE_NOT_FOUND:{
    cluster:'Проверка INCLUDE не прошла: в пакете нет тел файлов по ссылкам INCLUDE. Прикрепите недостающие .inc и нажмите Ответить.',
    item:s=>s.path?`Проверка INCLUDE не прошла: нет файла «${s.path}»${s.from?`, на него ссылается «${s.from}»`:''}. Прикрепите этот .inc к ответу.`:'Проверка INCLUDE не прошла: нет файла INCLUDE. Прикрепите недостающий .inc к ответу.',
  },
  INCLUDE_PATH_INVALID:{cluster:'Путь INCLUDE в пакете недопустим. Исправьте ссылку или приложите файл с корректным именем.', item:s=>`Недопустимый путь INCLUDE${s.path?` «${s.path}»`:''}${s.from?` в «${s.from}»`:''}.`},
  INCLUDE_PATH_UNSAFE:{cluster:'Путь INCLUDE выходит за пределы пакета. Оставьте только файлы внутри загруженного набора.', item:s=>`Небезопасный путь INCLUDE${s.path?` «${s.path}»`:''}. Приложите файл из того же пакета, без «..».`},
  INCLUDE_FILE_INVALID:{cluster:'Один из приложенных INCLUDE нельзя прочитать. Прикрепите его ещё раз как текстовый .inc.', item:'Приложенный INCLUDE без текста. Прикрепите файл .inc ещё раз.'},
  INCLUDE_CYCLE:{cluster:'В пакете цикл INCLUDE. Исправьте ссылки, чтобы файлы не включали друг друга по кругу.', item:s=>s.cycle.length?`Цикл INCLUDE: ${s.cycle.join(' → ')}. Исправьте ссылки в пакете.`:'В пакете цикл INCLUDE. Исправьте ссылки.'},
  INCLUDE_DEPTH_LIMIT:{cluster:'Слишком глубокая вложенность INCLUDE. Упростите пакет или приложите развёрнутый набор файлов.', item:'Слишком глубокая вложенность INCLUDE.'},
  INCLUDE_MULTIPLE_EXPANSION:{cluster:'Один INCLUDE раскрывается несколько раз. Проверьте дубли ссылок в пакете.', item:s=>`Файл «${s.path||s.from||'INCLUDE'}» раскрывается повторно.`},
  UNREACHABLE_INCLUDE_FILE:{cluster:'В пакете есть файл, до которого нет пути от корня. Укажите корневой schedule или уберите лишний файл.', item:s=>`Файл «${s.path||s.from}» не достигается от корня пакета.`},
  BASELINE_TEXT_REQUIRED:{cluster:'Нет текста корневого schedule. Прикрепите главный .inc / .data.', item:'Прикрепите корневой schedule (.inc или .data) — без него REVISE нельзя разобрать.'},
  BASELINE_REQUIRED:{cluster:'Для REVISE нужен предыдущий schedule. Прикрепите корневой .inc / .data.', item:'Прикрепите предыдущий schedule (.inc / .data) для режима REVISE.'},
  BASELINE_ANALYSIS_REQUIRED:{cluster:'Не разобран пакет schedule. Прикрепите полный набор файлов, включая INCLUDE.', item:'Сначала нужен разобранный пакет schedule. Прикрепите корневой файл и все INCLUDE.'},
  BASELINE_ROOT_REQUIRED:{cluster:'Не указан корневой файл пакета. Выберите главный .inc / .data.', item:'Укажите, какой .inc / .data главный в пакете.'},
  ROOT_PATH_INVALID:{cluster:'Имя корневого schedule недопустимо. Выберите файл с обычным именем .inc / .data.', item:'Недопустимое имя корневого schedule.'},
  ROOT_PATH_UNSAFE:{cluster:'Путь корневого schedule небезопасен. Приложите файл из пакета, без выхода наверх по каталогам.', item:'Небезопасный путь корневого schedule.'},
  ROOT_FILE_MISSING:{cluster:'В пакете нет корневого файла. Прикрепите главный .inc / .data.', item:'Нет корневого файла schedule в пакете.'},
  DUPLICATE_FILE_REF:{cluster:'В пакете два файла с одним путём. Оставьте один экземпляр.', item:s=>`Дублируется файл «${s.path||s.from}». Оставьте один.`},
  FILE_LIMIT:{cluster:'Слишком много файлов в пакете. Сократите набор INCLUDE.', item:'Превышен лимит файлов пакета.'},
  PACKAGE_SIZE_LIMIT:{cluster:'Пакет schedule слишком большой. Сократите набор файлов.', item:'Превышен размер пакета schedule.'},
  CST_NODE_LIMIT:{cluster:'В schedule слишком много блоков. Сократите пакет или уточните задачу.', item:'Слишком много блоков в schedule.'},
  CREATE_BASELINE_CONFLICT_REQUIRES_DECISION:{cluster:'Приложен baseline, но задача помечена как CREATE. Напишите: править существующий schedule или собрать новый и отбросить вложение.', item:'Напишите: REVISE существующего schedule или CREATE с отбрасыванием вложения.'},
  PRESERVATION_POLICY_REQUIRED:{cluster:'Для REVISE нужно сохранить не упомянутые записи. Подтвердите это в ответе или приложите полный пакет.', item:'Подтвердите сохранение не упомянутых записей (preserve_unmentioned) или уточните политику.'},
  SIMULATOR_PROFILE_NOT_APPROVED:{cluster:'Нужен профиль tNavigator 22.2 METRIC. Напишите, если задача для другого симулятора.', item:'Подтвердите tNavigator 22.2 METRIC или укажите другой согласованный профиль.'},
  METRIC_UNIT_SYSTEM_REQUIRED:{cluster:'Нужна метрическая система единиц. Подтвердите METRIC.', item:'Подтвердите unit_system METRIC.'},
  MODEL_START_DATE_REQUIRED:{cluster:'Нужна дата старта модели (YYYY-MM-DD).', item:'Укажите model_start_date в формате YYYY-MM-DD.'},
  FORECAST_START_DATE_REQUIRED:{cluster:'Нужна дата начала прогноза (YYYY-MM-DD).', item:'Укажите forecast_start в формате YYYY-MM-DD.'},
  FORECAST_END_DATE_REQUIRED:{cluster:'Нужна дата конца прогноза (YYYY-MM-DD).', item:'Укажите forecast_end в формате YYYY-MM-DD.'},
  FORECAST_HORIZON_INVALID:{cluster:'Конец прогноза раньше начала. Исправьте даты.', item:'forecast_end должен быть не раньше forecast_start.'},
  FORECAST_BEFORE_MODEL_START:{cluster:'Прогноз раньше старта модели. Исправьте даты.', item:'forecast_start не должен быть раньше model_start_date.'},
  HISTORY_INTERVAL_INCOMPLETE:{cluster:'Для истории нужны обе даты: начало и конец.', item:'Укажите и history_start, и history_end.'},
  HISTORY_INTERVAL_INVALID:{cluster:'Интервал истории задан неверно. Проверьте даты YYYY-MM-DD.', item:'Исправьте history_start / history_end.'},
  HISTORY_FORECAST_OVERLAP:{cluster:'История пересекается с прогнозом. Разведите интервалы.', item:'history_end не должен быть позже forecast_start.'},
  HISTORY_SCOPE_REQUIRES_INTERVAL:{cluster:'Для WCONHIST нужен интервал истории. Укажите history_start и history_end.', item:'Укажите history_start и history_end для WCONHIST.'},
  KEYWORD_SCOPE_UNRESOLVED:{cluster:'Не выбран набор keyword schedule. Напишите, какие секции менять (например WCONPROD, DATES).', item:'Укажите keyword, которые нужно менять.'},
  UNSUPPORTED_KEYWORD:{cluster:'В задаче есть keyword вне текущего allowlist. Уберите их или сузьте задачу.', item:s=>s.keywords.length?`Не поддерживаются keyword: ${s.keywords.join(', ')}.`:'Есть keyword вне allowlist. Уберите их из задачи.'},
  CREATE_CAPABILITY_SCOPE_REQUIRED:{cluster:'Для CREATE напишите, что именно собрать (какие скважины, контроли, период).', item:'Перечислите, что должно появиться в новом schedule.'},
  CREATE_REQUIRED_OUTPUTS_REQUIRED:{cluster:'Для CREATE укажите ожидаемый результат (например прогнозный .inc).', item:'Опишите, какой файл/результат нужен на выходе.'},
  REVISE_CHANGE_SCOPE_REQUIRED:{cluster:'Для REVISE напишите, что менять, а что оставить.', item:'Опишите requested_change_scope: что менять в существующем schedule.'},
  OBJECTIVE_REQUIRED:{cluster:'Нужна формулировка задачи своими словами.', item:'Сформулируйте измеримую цель и нужный результат.'},
  ACCEPTANCE_CRITERIA_REQUIRED:{cluster:'Нужен критерий приёмки: по чему понять, что задача сделана.', item:'Напишите, как проверить результат.'},
  EXPERT_KNOWLEDGE_AND_CATALOGUE_BINDING_REQUIRED:{cluster:'В базе знаний нет полной инструкции и схемы keyword. Загрузите корпус через Knowledge.', item:'Через Knowledge загрузите keyword_instruction и schema JSON в schedule_mvp.'},
  KEYWORD_INSTRUCTION_SCOPE_INCOMPLETE:{cluster:'Для части keyword нет экспертной инструкции. Догрузите карточки или сузьте набор keyword.', item:s=>s.keywords.length?`Нет инструкции для: ${s.keywords.join(', ')}. Загрузите карточки или уберите эти keyword.`:'Догрузите keyword_instruction для всех запрошенных keyword.'},
  SCHEDULE_RAG_EVIDENCE_REQUIRED:{cluster:'Builder не запущен: в базе нет полной экспертной инструкции или schema. Загрузите корпус через Knowledge.', item:'Через Knowledge загрузите active keyword_instruction и schema JSON в schedule_mvp.'},
  EXCEL_PROTOCOL_RAG_REQUIRED:{cluster:'Excel Extractor не запущен: нет operating protocol в базе. Загрузите excel_protocol через Knowledge.', item:'Через Knowledge загрузите protocol_instruction в excel_protocol.'},
  EXCEL_PROTOCOL_RAG_UNAVAILABLE:{cluster:'Excel Extractor не запущен: нет operating protocol в базе. Загрузите excel_protocol через Knowledge.', item:'Через Knowledge загрузите protocol_instruction в excel_protocol.'},
  SCHEDULE_RAG_UNAVAILABLE:{cluster:'Builder не запущен: в базе нет полной экспертной инструкции или schema. Загрузите корпус через Knowledge.', item:'Через Knowledge загрузите active keyword_instruction и schema JSON в schedule_mvp.'},
  STAGE_GATE_POLICY_INVALID:{cluster:'Политика гейтов не совпадает с утверждённой (attention 85 / HITL 70). Продолжите в этой задаче.', item:'Нужна политика attention_threshold=85, hitl_threshold=70.'},
  HISTORY_BEFORE_MODEL_START:{cluster:'История раньше старта модели. Исправьте даты.', item:'history_start не должен быть раньше model_start_date.'},
  INVALID_BUILD_MODE:{cluster:'Режим сборки не CREATE и не REVISE. Напишите, какой режим нужен.', item:'Укажите build_mode: CREATE или REVISE.'},
  EXCEL_WORKBOOK_REQUIRED:{cluster:'Нет книги Excel. Прикрепите файл .xlsx к ответу.', item:'Прикрепите книгу Excel (.xlsx или .xls) к ответу.'},
  EXCEL_CLARIFICATION_REQUIRED:{cluster:'Нужны ответы на уточнения по Excel. Напишите их в поле ответа.', item:'Ответьте на уточнения по таблице Excel.'},
  SCHEDULE_BUILD_CONTRACT_INVALID:{cluster:'Пакет задачи собран неверно. Создайте задачу заново из Activity с файлами.', item:'Пересоздайте задачу из Activity, приложив schedule и описание.'},
  SCHEDULE_BUILD_IDENTITY_REQUIRED:{cluster:'Потерян идентификатор задачи. Откройте задачу из списка слева или создайте заново.', item:'Продолжите из текущей задачи Activity, не копируя чужой пакет.'},
  SCHEDULE_BUILD_TASK_MISMATCH:{cluster:'Пакет относится к другой задаче. Работайте в текущей карточке Activity.', item:'Не переиспользуйте пакет другой задачи. Откройте нужную из списка.'},
  SCHEDULE_POLICY_VERSION_NOT_APPROVED:{cluster:'Версия политики schedule не совпадает. Продолжите в этой задаче или напишите, если нужен другой профиль.', item:'Нужна политика petroleum-schedule-policy-v1 либо явное согласование.'},
  EVIDENCE_GAP:{cluster:'Не хватает фактов для записи schedule. Укажите значения в ответе или приложите Excel.', item:s=>{
    const who=[s.keyword,s.entity,s.field].filter(Boolean).join(' / ');
    return who?`Не хватает ${who}. Напишите значение или приложите Excel.`:'Не хватает поля для schedule. Напишите значение или приложите Excel.';
  }},
  MALFORMED_EVIDENCE_GAP:{cluster:'Запрос уточнения по Excel собран неверно. Напишите недостающие факты вручную.', item:'Укажите недостающие поля вручную в ответе.'},
  STALLED_EVIDENCE_LOOP:{cluster:'Уточнение по Excel зациклилось на тех же данных. Напишите факты вручную, смените файл или сузьте задачу.', item:'Напишите недостающие факты вручную, приложите другую книгу Excel или сузьте задачу.'},
  EXCEL_EVIDENCE_BUDGET_EXHAUSTED:{cluster:'Исчерпан лимит запросов к Excel. Напишите недостающие факты вручную или сузьте задачу.', item:'Напишите недостающие поля вручную — автоматический запрос к Excel больше не запускается.'},
  BUILDER_ITERATION_BUDGET_EXHAUSTED:{cluster:'Исчерпан лимит повторов Schedule Builder. Напишите, как продолжать, или сузьте задачу.', item:'Напишите недостающие факты вручную или сузьте задачу — автоматический повтор Builder остановлен.'},
  SCHEDULE_BUILD_EXPECTED_VERSION_INVALID:{cluster:'Номер версии задачи некорректен. Откройте задачу заново из Activity.', item:'Продолжите из текущей карточки Activity, не копируя чужой пакет.'},
  SOURCE_ARTIFACT_REF_INVALID:{cluster:'Ссылка на исходный файл собрана неверно. Приложите файлы заново к ответу.', item:'Прикрепите исходные файлы к ответу ещё раз.'},
  SOURCE_ARTIFACT_LIMIT:{cluster:'Слишком много ссылок на исходные файлы. Сократите набор вложений.', item:'Сократите набор приложенных файлов.'},
  SCHEDULE_BUILD_REQUEST_TOO_LARGE:{cluster:'Пакет задачи слишком большой. Сократите текст schedule или набор INCLUDE.', item:'Сократите корневой schedule или число INCLUDE и повторите.'},
  EXPERT_KNOWLEDGE_CITATION_INVALID:{cluster:'Карточки знаний собраны неверно. Загрузите корпус заново через Knowledge.', item:'Через Knowledge загрузите keyword_instruction с полными citation-полями.'},
  OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE:{cluster:'Для запрошенного keyword нет схемы разбора. Догрузите schema или уберите keyword из задачи.', item:s=>s.keyword?`Для ${s.keyword} нет схемы разбора. Догрузите schema в schedule_mvp или уберите этот keyword.`:'Для keyword нет схемы разбора. Догрузите schema или сузьте набор keyword.'},
  OPAQUE_NODE_MUTATION_FORBIDDEN:{cluster:'Нельзя менять непрозрачный блок schedule без схемы. Догрузите schema или оставьте блок как есть.', item:s=>s.keyword?`Блок ${s.keyword} непрозрачный — его нельзя править без schema.`:'Непрозрачный блок schedule нельзя менять без schema.'},
  SOURCE_FACTS_WELL_IDENTITY_MISSING:{cluster:'Для сдвига дат ввода нет имени скважины. Укажите скважины или приложите Excel.', item:'Укажите имена скважин (Скважина / WELL) для commissioning.'},
  NEW_WELL_DATE_INVALID:{cluster:'Дата ввода новой скважины неверна. Напишите дату YYYY-MM-DD.', item:s=>s.entity?`Для скважины ${s.entity} укажите дату ввода в формате YYYY-MM-DD.`:'Укажите дату ввода новой скважины в формате YYYY-MM-DD.'},
  COMMISSIONING_DATE_INVALID:{cluster:'Дата ввода скважины неверна. Напишите дату YYYY-MM-DD.', item:s=>s.entity?`Для скважины ${s.entity} исправьте дату ввода (YYYY-MM-DD).`:'Исправьте дату ввода скважины (YYYY-MM-DD).'},
  DATES_STEP_REMOVED:{cluster:'Из schedule пропал шаг DATES, который был в baseline. Верните дату или подтвердите удаление.', item:'Верните пропавший шаг DATES или явно подтвердите его удаление.'},
  GROUP_REBIND_COMMISSIONING_DATE_MISSING:{cluster:'Для перепривязки групп нет даты ввода скважин. Укажите даты или приложите Excel.', item:'Укажите даты ввода скважин для group_membership_rebind.'},
  KEYWORD_SCHEMA_NOT_APPROVED:{cluster:'Для keyword нет утверждённой schema. Догрузите schema или уберите keyword из задачи.', item:s=>s.keyword?`Нет утверждённой schema для ${s.keyword}. Догрузите карточку или уберите keyword.`:'Нет утверждённой schema keyword. Догрузите каталог или сузьте задачу.'},
  DATES_NOT_STRICTLY_INCREASING:{cluster:'Даты DATES идут не по возрастанию. Исправьте порядок дат в schedule.', item:'Исправьте DATES: каждая следующая дата должна быть строго позже предыдущей.'},
  INCLUDE_TARGET_MISSING:{cluster:'В INCLUDE нет имени файла. Укажите путь .inc в кавычках.', item:s=>s.from?`В «${s.from}» у INCLUDE нет пути файла. Укажите имя .inc.`:'У INCLUDE нет пути файла. Укажите имя .inc в кавычках.'},
  SCHEDULE_TEXT_REQUIRED:{cluster:'Нет текста schedule. Прикрепите корневой .inc / .data.', item:'Прикрепите текст schedule (.inc или .data).'},
  INVALID_EXCEL_SPECIALIST_PACKET:{cluster:'Пакет для Excel Extractor собран неверно. Создайте задачу заново и приложите книгу .xlsx.', item:'Создайте задачу заново из Activity и приложите книгу Excel.'},
  INVALID_SCHEDULE_SPECIALIST_PACKET:{cluster:'Пакет задачи для Schedule Builder собран неверно. Создайте задачу заново из Activity.', item:'Создайте задачу заново из Activity, приложив schedule и описание.'},
  BASELINE_PACKAGE_INVALID:{cluster:'Пакет baseline разобран неверно. Прикрепите корневой файл и все INCLUDE заново.', item:'Прикрепите полный пакет schedule ещё раз.'},
  CHANGE_EFFECTIVE_FROM_REQUIRED:{cluster:'Нужна дата, с которой действует правка (change_effective_from).', item:'Укажите change_effective_from в формате YYYY-MM-DD.'},
  SCHEMA_CATALOGUE_NOT_APPROVED:{cluster:'Каталог schema keyword не утверждён. Загрузите утверждённый каталог через Knowledge.', item:'Через Knowledge загрузите утверждённый schema_catalogue в schedule_mvp.'},
  APPROVED_KEYWORD_SCHEMAS_REQUIRED:{cluster:'Нет утверждённых schema keyword. Загрузите каталог через Knowledge.', item:'Загрузите schema JSON для запрошенных keyword.'},
  PROFILE_NOT_APPROVED:{cluster:'Нужен профиль tNavigator 22.2 METRIC. Напишите, если задача для другого симулятора.', item:'Подтвердите tNavigator 22.2 METRIC или укажите другой согласованный профиль.'},
  REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL:{cluster:'Удаление записей из schedule нужно явно подтвердить в ответе.', item:s=>`Подтвердите удаление ${s.keyword||'записей'} или напишите, что их сохранить.`},
  NEW_WELLS_REQUIRE_HITL:{cluster:'Проверка новых скважин не прошла: нет траектории и спецификации. Прикрепите .dev и недостающие данные.', item:'Прикрепите WELLTRACK (.dev) и данные WELSPECS/COMPDATMD для новых скважин.'},
  NEW_WELL_CONTROL_LINE_REQUIRED:{cluster:'Для новой скважины нужна явная строка WCONPROD или WCONINJE. Значения по умолчанию не подставляются.', item:s=>s.entity?`Для скважины ${s.entity} укажите стартовую строку WCONPROD или WCONINJE — без GRAT/дебита по умолчанию.`:'Укажите стартовую строку WCONPROD или WCONINJE для новой скважины.'},
  NEW_WELL_TYPED_LINES_REQUIRED:{cluster:'Для новой скважины нужны явные строки keyword (WELSPECS, COMPDATMD, WCONPROD/WCONINJE). Ничего не выдумываем.', item:s=>s.entity?`Для скважины ${s.entity} пришлите typed-строки keyword, без шаблонов по умолчанию.`:'Пришлите typed-строки keyword для новой скважины.'},
  COMMISSIONING_CAPABILITY_REQUIRED:{cluster:'В Excel есть скважины и даты, но не сказано, что это сдвиг дат ввода. Напишите commissioning_date_retarget или уточните другую операцию.', item:s=>s.wells.length?`Для скважин ${s.wells.join(', ')} есть даты. Напишите, сдвигать даты ввода (commissioning_date_retarget) или это другая правка.`:'Есть скважины и даты. Напишите, сдвигать даты ввода или это другая правка.'},
  COMMISSIONING_FACTS_REQUIRED:{cluster:'Запрошен сдвиг дат ввода, но нет фактов скважина+дата. Приложите Excel или перечислите скважины и даты.', item:'Приложите факты скважина + дата ввода для commissioning_date_retarget.'},
  GROUP_REBIND_SPEC_REQUIRED:{cluster:'Для перепривязки групп нужен полный spec: скважины, parent_group, parent_of_parent, well_groups, контроль и дебит. Из текста DKS/FIELD не угадаем.', item:s=>`Укажите structured spec для group_membership_rebind${s.field?` (нет: ${s.field})`:''}: wells, parent_group, parent_of_parent, well_groups, control, rate.`},
  UNLISTED_WELLS_POLICY_REQUIRED:{cluster:'Проверка скважин вне Excel: нужно решить, сохранить их запуски или убрать. Напишите keep или remove и при необходимости имена скважин.', item:s=>s.wells.length?`В Excel нет скважин: ${s.wells.join(', ')}. Напишите: сохранить запуски (keep) или убрать (remove).`:'В Excel нет части скважин из schedule. Напишите keep или remove.'},
  SCHEDULE_PIPELINE_NOT_RELEASE_READY:{cluster:'Независимая проверка выпуска не прошла. Напишите, что исправить в черновике, своими словами.', item:'Черновик не готов к выпуску. Напишите, что проверить или доработать.'},
};
const hitlItemText=f=>{
  const s=hitlSlots(f);
  const spec=HITL_COPY[s.code];
  if(spec&&typeof spec.item==='function') return spec.item(s);
  if(spec&&typeof spec.item==='string') return spec.item;
  const bits=[s.path,s.from,s.keyword,s.entity,s.field].filter(Boolean);
  return bits.length?`Нужны данные: ${bits.join(', ')}. Прикрепите файл или напишите уточнение.`:HITL_GENERIC_ITEM;
};
const hitlClusterText=findings=>{
  const codes=[...new Set((Array.isArray(findings)?findings:[]).map(f=>hitlClean(f?.code)).filter(Boolean))];
  const clusters=codes.map(c=>HITL_COPY[c]&&HITL_COPY[c].cluster).filter(Boolean);
  const uniq=[...new Set(clusters)];
  if(uniq.length===1) return uniq[0];
  if(uniq.length>1) return uniq.slice(0,2).join(' ');
  return HITL_GENERIC_CLUSTER;
};
const hitlUniqueFindings=findings=>{
  const seen=new Set(), out=[];
  for(const f of (Array.isArray(findings)?findings:[])){
    if(!f||typeof f!=='object') continue;
    const key=hitlDedupeKey(f);
    if(seen.has(key)) continue;
    seen.add(key);
    out.push(f);
    if(out.length>=30) break;
  }
  return out;
};
const hitlCompileCopy=(opts={})=>{
  const unique=hitlUniqueFindings(opts.findings);
  const supplied=Array.isArray(opts.questions)?opts.questions.filter(q=>q&&typeof q==='object'):[];
  const gaps=Array.isArray(opts.gaps)?opts.gaps.filter(g=>g&&typeof g==='object'):[];
  let asks=[];
  const toAsk=(q,i,fallbackId)=>{
    const raw=hitlClean(q.text||q.question||q.message);
    const merged={...q, code:q.code||q.id, message:raw};
    const compiled=hitlLooksMachine(raw)?hitlItemText(merged):raw;
    const text=compiled||HITL_GENERIC_ITEM;
    const s=hitlSlots(merged);
    return {
      id:hitlClean(q.id)||`${fallbackId}_${i+1}`,
      text, question:text,
      required:q.required!==false,
      ...(q.type?{type:q.type}:{}),
      ...(s.code?{code:s.code}:{}),
      ...(s.path?{path:s.path}:{}),
      ...(s.from?{file_ref:s.from}:{}),
      ...(s.keyword?{keyword:s.keyword}:{}),
      ...(s.entity?{entity:s.entity}:{}),
    };
  };
  const suppliedMachine=supplied.length&&supplied.every(q=>hitlLooksMachine(q.text||q.question||q.message||q.code));
  if(supplied.length&&!(suppliedMachine&&unique.length)){
    asks=supplied.map((q,i)=>toAsk(q,i,'hitl'));
  } else if(gaps.length){
    asks=gaps.map((g,i)=>toAsk({...g, code:g.code||'EVIDENCE_GAP'}, i, 'schedule_gap'));
  } else {
    asks=unique.map((f,i)=>toAsk({...f, text:f.message}, i, 'schedule_finding'));
  }
  const seen=new Set();
  asks=asks.filter(a=>{
    const k=a.text;
    if(!k||seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0,20);
  if(!asks.length) asks=[{id:'hitl_needed', text:HITL_GENERIC_ITEM, question:HITL_GENERIC_ITEM, required:true}];
  const preferred=hitlClean(opts.summary);
  let user_message=hitlLooksMachine(preferred)?hitlClusterText(unique):preferred;
  const stageLabel=HITL_STAGE[hitlClean(opts.stage)]||'';
  if(stageLabel&&user_message&&!user_message.includes(stageLabel)) user_message=`${stageLabel} не прошла. ${user_message}`;
  return {user_message, questions:asks};
};
""".strip()
