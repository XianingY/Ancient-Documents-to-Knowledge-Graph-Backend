
class EntityResolver:
    """
    实体消歧解析器
    """
    
    @staticmethod
    def calculate_similarity(node1_attrs, node2_attrs):
        """
        计算两个节点的相似度
        """
        score = 0.0
        
        # 1. 姓名匹配
        name1 = node1_attrs.get("name", "")
        name2 = node2_attrs.get("name", "")
        if not name1 or not name2:
            return 0.0
        
        if name1 == name2:
            score += 0.4
        elif name1 in name2 or name2 in name1:
            score += 0.2
        else:
            return 0.0
            
        # 2. 角色匹配
        role1 = node1_attrs.get("role", "")
        role2 = node2_attrs.get("role", "")
        if role1 == role2:
            score += 0.2
        
        # 3. 时间匹配
        time1 = node1_attrs.get("time_ad")
        time2 = node2_attrs.get("time_ad")
        
        if time1 and time2:
            try:
                diff = abs(int(time1) - int(time2))
                if diff <= 5: # 5年内
                    score += 0.2
                elif diff <= 20: # 20年内
                    score += 0.1
            except:
                pass 
        
        # 4. 地点匹配
        loc1 = node1_attrs.get("location", "")
        loc2 = node2_attrs.get("location", "")
        if loc1 and loc2:
            if loc1 == loc2:
                score += 0.2
            elif loc1 in loc2 or loc2 in loc1:
                score += 0.15
                
        return score

    @staticmethod
    def resolve_entities(raw_nodes):
        """
        执行实体消歧
        """
        merged_entities = []
            
        for node in raw_nodes:
            matched = False
            best_score = 0
            best_match_idx = -1
            
            for idx, entity in enumerate(merged_entities):
                representative = entity["instances"][0]
                
                node_attrs = {
                    "name": node["original_name"],
                    "role": node["role"],
                    "time_ad": node["time_ad"],
                    "location": node["location"]
                }
                
                rep_attrs = {
                    "name": representative["original_name"],
                    "role": representative["role"],
                    "time_ad": representative["time_ad"],
                    "location": representative["location"]
                }
                
                score = EntityResolver.calculate_similarity(node_attrs, rep_attrs)
                
                if score >= 0.5 and score > best_score:
                    best_score = score
                    best_match_idx = idx
                    matched = True
            
            if matched:
                merged_entities[best_match_idx]["instances"].append(node)
            else:
                merged_entities.append({
                    "id": f"entity_{len(merged_entities)}",
                    "standard_name": node["original_name"],
                    "role": node["role"],
                    "instances": [node]
                })
        
        return merged_entities
