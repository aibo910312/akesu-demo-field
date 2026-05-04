"""用高德API获取阿克苏地区9县市 乡镇+村/社区精准坐标"""
import json
import os
import re
import time
import urllib.request
import urllib.parse

GAODE_KEY = "90684e50f841f63404c0cf7ca7dc9789"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE_DIR, "townships.json")

# 阿克苏地区9县市配置
REGIONS = [
    {
        "name": "阿克苏市",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (79.80, 80.60),
        "lat_range": (40.80, 41.40),
        "townships": [
            "栏杆街道", "英巴扎街道", "红桥街道", "新城街道", "南城街道",
            "柯柯牙街道", "多浪街道", "喀勒塔勒镇", "阿依库勒镇",
            "依干其乡", "拜什吐格曼乡", "托普鲁克乡", "库木巴什乡",
        ],
    },
    {
        "name": "库车市",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (82.30, 83.80),
        "lat_range": (41.00, 42.20),
        "townships": [
            "热斯坦街道", "萨克萨克街道", "新城街道", "东城街道", "乌恰街道",
            "伊西哈拉镇", "乌尊镇", "牙哈镇", "齐满镇", "哈尼喀塔木乡",
            "阿拉哈格镇", "墩阔坦镇", "雅克拉镇", "阿克吾斯塘乡",
            "比西巴格乡", "玉奇吾斯塘乡", "塔里木乡",
        ],
    },
    {
        "name": "温宿县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (79.50, 81.00),
        "lat_range": (40.80, 42.00),
        "townships": [
            "温宿镇", "吐木秀克镇", "克孜勒镇", "阿热勒镇", "佳木镇",
            "托乎拉乡", "恰格拉克乡", "古勒阿瓦提乡", "博孜墩柯尔克孜族乡",
            "柯柯牙管理区",
        ],
    },
    {
        "name": "沙雅县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (81.50, 83.50),
        "lat_range": (40.00, 41.50),
        "townships": [
            "沙雅镇", "托依堡勒迪镇", "红旗镇", "英买力镇",
            "古勒巴格镇", "海楼镇", "努尔巴格乡", "塔里木乡",
        ],
    },
    {
        "name": "新和县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (81.50, 82.80),
        "lat_range": (41.20, 41.80),
        "townships": [
            "新和镇", "尤鲁都斯巴格镇", "依其艾日克镇", "排先拜巴扎乡",
            "塔什艾日克镇", "玉奇喀特镇", "渭干乡", "塔木托格拉克乡",
        ],
    },
    {
        "name": "拜城县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (80.50, 82.50),
        "lat_range": (41.50, 42.50),
        "townships": [
            "拜城镇", "铁热克镇", "黑英山乡", "赛里木镇", "克孜尔乡",
            "康其乡", "布隆乡", "亚吐尔乡", "托克逊乡", "大桥乡", "老虎台乡",
        ],
    },
    {
        "name": "乌什县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (78.50, 80.00),
        "lat_range": (41.00, 42.00),
        "townships": [
            "乌什镇", "阿合雅镇", "阿克托海依乡", "亚科瑞克乡",
            "阿恰塔格乡", "依麻木镇", "英阿瓦提乡", "亚曼苏柯尔克孜族乡",
            "奥特贝希乡",
        ],
    },
    {
        "name": "阿瓦提县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (79.80, 81.00),
        "lat_range": (39.50, 40.80),
        "townships": [
            "阿瓦提镇", "乌鲁却勒镇", "拜什艾日克镇", "阿依巴格乡",
            "塔木托格拉克乡", "英艾日克乡", "多浪乡", "巴格托格拉克乡",
        ],
    },
    {
        "name": "柯坪县",
        "city": "阿克苏",
        "province": "新疆维吾尔自治区阿克苏地区",
        "lng_range": (78.80, 79.80),
        "lat_range": (40.30, 40.90),
        "townships": [
            "柯坪镇", "盖孜力克镇", "玉尔其乡", "阿恰勒镇", "启浪乡",
        ],
    },
]

# 所有乡镇名（用于村名清理）
ALL_TOWNSHIPS = []
for r in REGIONS:
    ALL_TOWNSHIPS.extend(r["townships"])


def gaode_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_center(s):
    lng, lat = s.split(",")
    return [round(float(lng), 6), round(float(lat), 6)]


def in_bounds(coord, region):
    return (region["lng_range"][0] <= coord[0] <= region["lng_range"][1] and
            region["lat_range"][0] <= coord[1] <= region["lat_range"][1])


def extract_village_name(raw_name):
    """从村委会POI名称中提取村名"""
    name = re.sub(r'^(新疆维吾尔自治区)?(阿克苏地区)?', '', raw_name)
    # 去掉区县名
    for r in REGIONS:
        name = name.replace(r["name"], '')
    # 去掉乡镇名
    for tw in ALL_TOWNSHIPS:
        name = name.replace(tw, '')
    name = re.sub(r'^[一-龥]{1,5}(?:街道|镇|乡)', '', name)
    name = re.sub(r'^(办事处|管委会|管理处)', '', name)
    # "xx村村民委员会"
    m = re.search(r'([^村]{1,5}村)(?:村民委员会|民委员会|委员会|委会)', name)
    if m:
        return m.group(1)
    m = re.search(r'([^村]{1,5}村)$', name)
    if m:
        return m.group(1)
    m = re.search(r'([^社]{1,5}社区)', name)
    if m:
        return m.group(1)
    return None


def main():
    import math

    def distance(c1, c2):
        return math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2)

    result = {}

    for region in REGIONS:
        region_name = region["name"]
        print(f"\n{'='*50}")
        print(f"  {region_name}")
        print(f"{'='*50}")

        # -- Step 1: 获取乡镇坐标（行政区划接口）--
        print(f"\n--- Step 1: 获取{region_name}乡镇坐标 ---")
        time.sleep(0.3)
        params = urllib.parse.urlencode({
            "keywords": region_name, "subdistrict": 1,
            "key": GAODE_KEY, "extensions": "base",
        })
        url = f"https://restapi.amap.com/v3/config/district?{params}"
        data = gaode_get(url)

        api_map = {}
        if data.get("districts"):
            for d in data["districts"][0].get("districts", []):
                api_map[d["name"]] = parse_center(d["center"])

        township_coords = {}
        for tw in region["townships"]:
            if tw in api_map:
                township_coords[tw] = api_map[tw]
                print(f"  {tw}: {api_map[tw]}")
            else:
                # fallback: 地理编码
                time.sleep(0.3)
                geo_params = urllib.parse.urlencode({
                    "address": f"{region['province']}{region_name}{tw}",
                    "key": GAODE_KEY, "city": "阿克苏",
                })
                geo_url = f"https://restapi.amap.com/v3/geocode/geo?{geo_params}"
                geo_data = gaode_get(geo_url)
                if geo_data.get("geocodes"):
                    coord = parse_center(geo_data["geocodes"][0]["location"])
                    township_coords[tw] = coord
                    print(f"  {tw}: {coord} (geocode fallback)")
                else:
                    print(f"  {tw}: FAILED")

        # -- Step 2: 按乡镇逐个搜索村委会POI --
        print(f"\n--- Step 2: 按乡镇搜索{region_name}村委会/社区 ---")
        all_pois_by_tw = {tw: [] for tw in region["townships"]}
        for tw in region["townships"]:
            tw_pois = []
            for keyword in [f"{region_name}{tw} 村委会", f"{region_name}{tw} 社区居委会"]:
                for page in range(1, 20):
                    time.sleep(0.3)
                    params = urllib.parse.urlencode({
                        "keywords": keyword,
                        "key": GAODE_KEY,
                        "offset": 50,
                        "page": page,
                        "city": "阿克苏",
                        "citylimit": "true",
                    })
                    url = f"https://restapi.amap.com/v3/place/text?{params}"
                    data = gaode_get(url)
                    pois = data.get("pois", [])
                    if not pois:
                        break
                    tw_pois.extend(pois)
                    total = int(data.get("count", 0))
                    if len(tw_pois) >= total:
                        break
            all_pois_by_tw[tw] = tw_pois
            print(f"  {tw}: {len(tw_pois)} POIs")

        # -- Step 3: 解析村名 --
        villages_by_township = {tw: {} for tw in region["townships"]}

        for tw in region["townships"]:
            seen = set()
            for poi in all_pois_by_tw[tw]:
                raw_name = poi.get("name", "")
                location = poi.get("location", "")
                if not location:
                    continue

                coord = parse_center(location)
                village_name = extract_village_name(raw_name)
                if not village_name or village_name in seen:
                    continue
                seen.add(village_name)
                villages_by_township[tw][village_name] = coord

        # 写入结果
        for tw in region["townships"]:
            result[tw] = {
                "coord": township_coords.get(tw, [0, 0]),
                "villages": villages_by_township.get(tw, {}),
            }

        # 统计
        total_v = sum(len(villages_by_township[tw]) for tw in region["townships"])
        print(f"\n  {region_name}: {len(region['townships'])}乡镇, {total_v}村")
        for tw in region["townships"]:
            vc = len(villages_by_township[tw])
            print(f"    {tw}: {township_coords.get(tw, 'N/A')} | {vc}村")

    # -- 保存 --
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    grand_total = sum(len(v["villages"]) for v in result.values())
    print(f"\n{'='*50}")
    print(f"全部完成: {len(result)}乡镇, {grand_total}村/社区")
    print(f"已写入: {OUTPUT}")


if __name__ == "__main__":
    main()
