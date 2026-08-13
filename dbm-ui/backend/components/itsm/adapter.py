# -*- coding: utf-8 -*-
"""
TencentBlueKing is pleased to support the open source community by making 蓝鲸智云-DB管理系统(BlueKing-BK-DBM) available.
Copyright (C) 2017-2023 THL A29 Limited, a Tencent company. All rights reserved.
Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
You may obtain a copy of the License at https://opensource.org/licenses/MIT
Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
"""

import json
import time

from backend import env

from .client import ItsmApi, ItsmV4Api


class _ItsmApiAdapter:
    """根据环境变量切换 V3/V4 接口，对调用方屏蔽版本差异。"""

    V4 = "v4"
    V4_SN_PREFIX = "ITSM_V4:"

    @property
    def api_version(self):
        return str(env.ITSM_API_VERSION).lower()

    @property
    def use_v4(self):
        return self.api_version == self.V4

    @classmethod
    def _is_legacy_ticket_sn(cls, sn):
        """旧版的存的sn不是字符串'ITSM_V4'开头"""
        if isinstance(sn, (list, tuple)):
            sn = sn[0] if sn else ""
        sn = str(sn)
        return not str(sn).startswith(cls.V4_SN_PREFIX)

    @classmethod
    def _format_v4_ticket_sn(cls, ticket_id):
        return f"{cls.V4_SN_PREFIX}{ticket_id}"

    @classmethod
    def _get_v4_ticket_id(cls, sn):
        if isinstance(sn, (list, tuple)):
            sn = sn[0] if sn else ""
        sn = str(sn)
        return sn[len(cls.V4_SN_PREFIX) :] if sn.startswith(cls.V4_SN_PREFIX) else sn

    @classmethod
    def _format_v4_ticket_id_params(cls, params):
        ticket_id = cls._get_v4_ticket_id(params.get("sn"))
        return {**params, "id": ticket_id}

    @classmethod
    def _format_v4_ticket_log_params(cls, params):
        ticket_id = cls._get_v4_ticket_id(params.get("sn"))
        return {**{key: value for key, value in params.items() if key not in ["sn", "id"]}, "ticket_id": ticket_id}

    @classmethod
    def _normalize_v4_ticket_detail(cls, detail):
        normalized_detail = {
            **detail,
            "current_status": detail.get("status", "").upper(),
            "ticket_url": detail.get("frontend_url"),
        }
        for step in normalized_detail["current_steps"]:
            if "state_id" not in step:
                step["state_id"] = step.get("id") or step.get("task_id")
        return normalized_detail

    @classmethod
    def _normalize_v4_ticket_approval_result(cls, detail):
        return {
            "update_at": detail.get("updated_at"),
            "current_status": detail.get("status", "").upper(),
            "approve_result": detail.get("approve_result"),
            "ticket_url": detail.get("frontend_url"),
        }

    @classmethod
    def _normalize_v4_ticket_logs(cls, logs):
        log_list = logs.get("items", [])
        return {"logs": [{**log, "message": log.get("action_display")} for log in log_list]}

    @classmethod
    def _get_v4_approve_action(cls, params):
        fields = {field.get("key"): field.get("value") for field in params.get("fields", [])}
        is_approved = fields.get("is_approved")
        if is_approved is None and params.get("fields"):
            is_approved = params["fields"][0].get("value")
        if isinstance(is_approved, str):
            is_approved = json.loads(is_approved.lower())
        return "approve" if is_approved else "refuse"

    @classmethod
    def _format_v4_deliver_processors(cls, processors):
        if isinstance(processors, str):
            processors = [processor.strip() for processor in processors.split(",") if processor.strip()]
        elif processors is None:
            processors = []
        return [{"id": processor, "type": "user"} for processor in processors]

    @classmethod
    def _format_v4_handle_ticket_params(cls, params):
        action_type = params.get("action_type")
        action_method = cls._get_v4_approve_action(params) if action_type == "TRANSITION" else action_type.lower()
        action_params = {"desc": params.get("remark")}
        if action_type == "DELIVER":
            action_params = {
                "to": cls._format_v4_deliver_processors(params.get("processors")),
                "desc": params.get("remark"),
            }
        if action_type == "WITHDRAW":
            action_params["target_activity"] = params.get("activity_key")
        return {
            "ticket_id": cls._get_v4_ticket_id(params.get("sn")),
            "task_id": params.get("task_id"),
            "operator": params.get("operator") or params.get("bk_username"),
            "form_data": params.get("form_data", {}),
            "action": {
                "method": action_method,
                "params": action_params,
            },
        }

    def create_ticket(self, params, *args, **kwargs):
        if self.use_v4:
            data = ItsmV4Api.create_ticket(params, *args, **kwargs)
            if "id" in data:
                data = {**data, "sn": self._format_v4_ticket_sn(data["id"])}
                data.pop("id")
            return data
        return ItsmApi.create_ticket(params, *args, **kwargs)

    def ticket_approval_result(self, params, *args, **kwargs):
        if self._is_legacy_ticket_sn(params.get("sn")):
            return ItsmApi.ticket_approval_result(params, *args, **kwargs)

        v4_params = {**params, "id": self._get_v4_ticket_id(params.get("sn"))}
        detail = ItsmV4Api.get_ticket_detail(v4_params, *args, **kwargs)
        if detail.get("status", "").lower() == "draft":
            # 提单时候,itsm那边状态有可能还未更新,此时查询拿到的结果可能为draft,会引起报错,所以需要重新查一下
            time.sleep(1)
            detail = ItsmV4Api.get_ticket_detail(v4_params, *args, **kwargs)
        return [self._normalize_v4_ticket_approval_result(detail)]

    def get_ticket_logs(self, params, *args, **kwargs):
        if self._is_legacy_ticket_sn(params.get("sn")):
            return ItsmApi.get_ticket_logs(params, *args, **kwargs)

        v4_params = self._format_v4_ticket_log_params(params)
        logs = ItsmV4Api.get_ticket_logs(v4_params, *args, **kwargs)
        return self._normalize_v4_ticket_logs(logs)

    def get_ticket_info(self, params, *args, **kwargs):
        if self._is_legacy_ticket_sn(params.get("sn")):
            return ItsmApi.get_ticket_info(params, *args, **kwargs)

        detail = ItsmV4Api.get_ticket_detail(self._format_v4_ticket_id_params(params), *args, **kwargs)
        return self._normalize_v4_ticket_detail(detail)

    def operate_node(self, params, *args, **kwargs):
        if self._is_legacy_ticket_sn(params.get("sn")):
            return ItsmApi.operate_node(params, *args, **kwargs)
        return ItsmV4Api.handle_ticket(self._format_v4_handle_ticket_params(params), *args, **kwargs)

    def operate_ticket(self, params, *args, **kwargs):
        if self._is_legacy_ticket_sn(params.get("sn")):
            return ItsmApi.operate_ticket(params, *args, **kwargs)
        return ItsmV4Api.handle_ticket(self._format_v4_handle_ticket_params(params), *args, **kwargs)

    def migrate_system(self, params, *args, **kwargs):
        return ItsmV4Api.migrate_system(params, *args, **kwargs)

    def get_ticket_status(self, params, *args, **kwargs):
        return ItsmApi.get_ticket_status(params, *args, **kwargs)

    def get_service_catalogs(self, params, *args, **kwargs):
        return ItsmApi.get_service_catalogs(params, *args, **kwargs)

    def get_services(self, params, *args, **kwargs):
        return ItsmApi.get_services(params, *args, **kwargs)

    def create_service_catalog(self, params, *args, **kwargs):
        return ItsmApi.create_service_catalog(params, *args, **kwargs)

    def import_service(self, params, *args, **kwargs):
        return ItsmApi.import_service(params, *args, **kwargs)

    def update_service(self, params, *args, **kwargs):
        return ItsmApi.update_service(params, *args, **kwargs)


ItsmApiAdapter = _ItsmApiAdapter()
