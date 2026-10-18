import pytest
import requests
import os
import time
from typing import Optional

# 需要真实后端服务的联调用例；默认 pytest 配置会跳过该目录。
pytestmark = pytest.mark.live_api

# API 基础 URL
BASE_URL = os.getenv(
    "API_TEST_BASE_URL",
    "http://localhost:3000/api/v1",
)

# 测试数据
TEST_USERNAME = "test_user"
TEST_PASSWORD = "test_password123"
TEST_IMAGE_PATH = "api_test/test_img/test_img.jpg"

# 全局变量存储测试数据
access_token: Optional[str] = None
user_id: Optional[int] = None
image_id: Optional[int] = None
ocr_result_id: Optional[int] = None
structured_result_id: Optional[int] = None
relation_graph_id: Optional[int] = None
multi_task_id: Optional[int] = None
multi_relation_graph_id: Optional[int] = None


class TestAuthentication:
    """认证相关测试"""
    
    def test_01_register(self):
        """测试用户注册"""
        global user_id
        response = requests.post(
            f"{BASE_URL}/auth/register",
            json={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD
            }
        )
        assert response.status_code == 200, f"注册失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["username"] == TEST_USERNAME
        user_id = data["userId"]
        print(f"用户注册成功，用户ID: {user_id}")
    
    def test_02_login(self):
        """测试用户登录"""
        global access_token, user_id
        response = requests.post(
            f"{BASE_URL}/auth/login",
            json={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD
            }
        )
        assert response.status_code == 200, f"登录失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["token_type"] == "bearer"
        access_token = data["access_token"]
        user_id = data["user_id"]
        print(f"用户登录成功，Token已获取")
    
    def test_03_get_user_info(self):
        """测试获取用户信息"""
        response = requests.get(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取用户信息失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["user"]["username"] == TEST_USERNAME
        print(f"获取用户信息成功")
    
    def test_04_refresh_token(self):
        """测试刷新 Token"""
        global access_token
        response = requests.post(
            f"{BASE_URL}/auth/refresh",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"刷新Token失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        new_token = data["access_token"]
        assert new_token != access_token
        access_token = new_token
        print(f"Token刷新成功")
    
    def test_05_logout(self):
        """测试退出登录"""
        response = requests.post(
            f"{BASE_URL}/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"退出登录失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"退出登录成功")


class TestImages:
    """图片管理相关测试"""
    
    def test_01_upload_image(self):
        """测试上传图片"""
        global image_id, access_token
        
        # 重新登录获取有效token
        login_response = requests.post(
            f"{BASE_URL}/auth/login",
            json={
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD
            }
        )
        access_token = login_response.json()["access_token"]
        
        # 确保图片文件存在
        if not os.path.exists(TEST_IMAGE_PATH):
            pytest.skip(f"测试图片不存在: {TEST_IMAGE_PATH}")
        
        with open(TEST_IMAGE_PATH, "rb") as f:
            files = {"image": f}
            response = requests.post(
                f"{BASE_URL}/images/upload",
                files=files,
                params={"user_id": user_id},
                headers={"Authorization": f"Bearer {access_token}"}
            )
        
        assert response.status_code == 200, f"上传图片失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        image_id = data["imageId"]
        print(f"图片上传成功，图片ID: {image_id}")
    
    def test_02_get_image(self):
        """测试获取图片"""
        response = requests.get(
            f"{BASE_URL}/images/{image_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取图片失败: {response.text}"
        assert len(response.content) > 0
        print(f"图片获取成功")
    
    def test_03_get_user_images(self):
        """测试获取用户图片列表"""
        response = requests.get(
            f"{BASE_URL}/users/images",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取用户图片列表失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["data"]["total"] > 0
        assert image_id in data["data"]["ids"]
        print(f"获取用户图片列表成功，共 {data['data']['total']} 张")


class TestOCR:
    """OCR 相关测试"""
    
    def test_01_perform_ocr(self):
        """测试执行 OCR"""
        response = requests.post(
            f"{BASE_URL}/images/{image_id}/ocr",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"执行OCR失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"OCR 任务已提交")
        
        # 等待 OCR 处理
        time.sleep(2)
    
    def test_02_get_image_ocr_results(self):
        """测试获取图片的 OCR 结果列表"""
        response = requests.get(
            f"{BASE_URL}/images/{image_id}/ocr-results",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取OCR结果列表失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        
        if data["data"]["ids"]:
            global ocr_result_id
            ocr_result_id = data["data"]["ids"][0]
            print(f"获取OCR结果列表成功，共 {data['data']['total']} 条")
        else:
            print(f"⚠ 暂无OCR结果")
    
    def test_03_get_ocr_result(self):
        """测试获取特定 OCR 结果"""
        if not ocr_result_id:
            pytest.skip("未找到OCR结果")
        
        response = requests.get(
            f"{BASE_URL}/ocr-results/{ocr_result_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取OCR结果失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["data"]["image_id"] == image_id
        print(f"获取OCR结果成功")


class TestStructuredResults:
    """结构化结果相关测试"""
    
    def test_01_create_structured_result(self):
        """测试创建结构化结果"""
        global structured_result_id
        
        if not ocr_result_id:
            pytest.skip("未找到OCR结果")
        
        response = requests.post(
            f"{BASE_URL}/structured-results",
            json={"ocr_result_id": ocr_result_id},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"创建结构化结果失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"结构化分析任务已提交")
        
        # 等待分析处理
        time.sleep(2)
    
    def test_02_get_ocr_structured_results(self):
        """测试获取 OCR 的结构化结果列表"""
        response = requests.get(
            f"{BASE_URL}/ocr-results/{ocr_result_id}/structured-results",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取结构化结果列表失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        
        if data["data"]["ids"]:
            global structured_result_id
            structured_result_id = data["data"]["ids"][0]
            print(f"获取结构化结果列表成功，共 {data['data']['total']} 条")
        else:
            print(f"⚠ 暂无结构化结果")
    
    def test_03_get_structured_result(self):
        """测试获取特定结构化结果"""
        if not structured_result_id:
            pytest.skip("未找到结构化结果")
        
        response = requests.get(
            f"{BASE_URL}/structured-results/{structured_result_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取结构化结果失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"获取结构化结果成功")


class TestRelationGraphs:
    """关系图相关测试"""
    
    def test_01_create_relation_graph(self):
        """测试创建关系图"""
        global relation_graph_id
        
        if not structured_result_id:
            pytest.skip("未找到结构化结果")
        
        response = requests.post(
            f"{BASE_URL}/relation-graphs",
            json={"structured_result_id": structured_result_id},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"创建关系图失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"关系图分析任务已提交")
        
        # 等待分析处理
        time.sleep(2)
    
    def test_02_get_structured_result_relation_graphs(self):
        """测试获取结构化结果的关系图列表"""
        response = requests.get(
            f"{BASE_URL}/structured-results/{structured_result_id}/relation-graphs",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取关系图列表失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        
        if data["data"]["ids"]:
            global relation_graph_id
            relation_graph_id = data["data"]["ids"][0]
            print(f"获取关系图列表成功，共 {data['data']['total']} 条")
        else:
            print(f"⚠ 暂无关系图")
    
    def test_03_get_relation_graph(self):
        """测试获取特定关系图"""
        if not relation_graph_id:
            pytest.skip("未找到关系图")
        
        response = requests.get(
            f"{BASE_URL}/relation-graphs/{relation_graph_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取关系图失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"获取关系图成功")


class TestMultiTasks:
    """多任务相关测试"""
    
    def test_01_create_multi_task(self):
        """测试创建多任务"""
        global multi_task_id
        
        if not structured_result_id:
            pytest.skip("未找到结构化结果")
        
        response = requests.post(
            f"{BASE_URL}/multi-tasks",
            json={"structured_result_ids": [structured_result_id]},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"创建多任务失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        multi_task_id = data["multi_task_id"]
        print(f"多任务创建成功，任务ID: {multi_task_id}")
    
    def test_02_get_multi_task(self):
        """测试获取多任务"""
        response = requests.get(
            f"{BASE_URL}/multi-tasks/{multi_task_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取多任务失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["data"]["id"] == multi_task_id
        print(f"获取多任务成功")
    
    def test_03_get_user_multi_tasks(self):
        """测试获取用户的多任务列表"""
        response = requests.get(
            f"{BASE_URL}/users/multi-tasks",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取用户多任务列表失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["data"]["total"] > 0
        assert multi_task_id in data["data"]["ids"]
        print(f"获取用户多任务列表成功，共 {data['data']['total']} 个")


class TestMultiRelationGraphs:
    """跨文档关系图相关测试"""
    
    def test_01_create_multi_relation_graph(self):
        """测试创建跨文档关系图"""
        global multi_relation_graph_id
        
        if not multi_task_id:
            pytest.skip("未找到多任务")
        
        response = requests.post(
            f"{BASE_URL}/multi-relation-graphs",
            json={"multi_task_id": multi_task_id},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"创建跨文档关系图失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"跨文档分析任务已提交")
        
        # 等待分析处理
        time.sleep(2)
    
    def test_02_get_multi_task_relation_graphs(self):
        """测试获取多任务的跨文档关系图列表"""
        response = requests.get(
            f"{BASE_URL}/multi-tasks/{multi_task_id}/multi-relation-graphs",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取跨文档关系图列表失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        
        if data["data"]["ids"]:
            global multi_relation_graph_id
            multi_relation_graph_id = data["data"]["ids"][0]
            print(f"获取跨文档关系图列表成功，共 {data['data']['total']} 条")
        else:
            print(f"⚠ 暂无跨文档关系图")
    
    def test_03_get_multi_relation_graph(self):
        """测试获取特定跨文档关系图"""
        if not multi_relation_graph_id:
            pytest.skip("未找到跨文档关系图")
        
        response = requests.get(
            f"{BASE_URL}/multi-relation-graphs/{multi_relation_graph_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"获取跨文档关系图失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        print(f"获取跨文档关系图成功")


class TestUserUpdate:
    """用户信息更新测试"""
    
    def test_update_user_info(self):
        """测试更新用户信息"""
        new_username = f"{TEST_USERNAME}_updated"
        response = requests.put(
            f"{BASE_URL}/users/me",
            json={"username": new_username},
            headers={"Authorization": f"Bearer {access_token}"}
        )
        assert response.status_code == 200, f"更新用户信息失败: {response.text}"
        data = response.json()
        assert data["success"] == True
        assert data["user"]["username"] == new_username
        print(f"用户信息更新成功")


if __name__ == "__main__":
    # 运行测试：pytest test_api.py -v -s
    print("请确保 API 服务已启动在 http://localhost:8000")
    print("运行测试: pytest test_api.py -v -s")
