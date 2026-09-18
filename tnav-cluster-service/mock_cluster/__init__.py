"""Мок гидродинамического кластера: дерево модели, заглушка ``tNavigator-con`` и SSH-сервер для тестов.

Лаборатория и тесты работают с тем же кодом сервиса, что и поле: меняется только транспорт
(``TNAV_TRANSPORT=local`` — песочница, ``ssh`` — кластер) и путь к исполняемому файлу в ``TNAV_CLI_COMMAND``.
"""

from .model_tree import build_model_tree, main_schedule_path

__all__ = ["build_model_tree", "main_schedule_path"]
