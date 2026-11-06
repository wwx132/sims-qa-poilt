from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = ""
    qwen_model: str = ""
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""
    es_url: str = ""
    es_index: str = ""
    es_username: str = ""
    es_password: str = ""
    es_verify_tls: bool = True
    es_vector_field: str = "embedding"
    es_keyword_field: str = "search_text"
    keyword_top_k: int = 10
    vector_top_k: int = 10
    rrf_k: int = 60
    fused_top_k: int = 5
    doris_host: str = ""
    doris_port: int = 9030
    doris_user: str = ""
    doris_password: str = ""
    doris_database: str = ""
    doris_timeout_seconds: int = 15
    max_result_rows: int = 50
    trace_log_path: str = "logs/trace.jsonl"
    trace_log_questions: bool = False

    def missing_services(self) -> list[str]:
        required = {
            "QWEN_API_KEY": self.qwen_api_key,
            "QWEN_MODEL": self.qwen_model,
            "EMBEDDING_API_KEY": self.embedding_api_key,
            "EMBEDDING_MODEL": self.embedding_model,
            "ES_URL": self.es_url,
            "ES_INDEX": self.es_index,
            "ES_USERNAME": self.es_username,
            "ES_PASSWORD": self.es_password,
            "DORIS_HOST": self.doris_host,
            "DORIS_USER": self.doris_user,
            "DORIS_DATABASE": self.doris_database,
        }
        return [name for name, value in required.items() if not value]
