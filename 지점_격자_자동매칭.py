# -*- coding: utf-8 -*-
"""
Created on Thu Jun 19 17:57:26 2025
@author: sang

포인트에서 반경 40km 이내의 모델 격자만 대상으로
최근접 "최초 20개 타임스텝 모두 정상값" 격자만 찾기

요구사항 반영:
- model 폴더에서 모델 파일을 못 가져오면(없거나, 열기 실패 등) 어떤 모델인지 알리고,
  해당 모델은 제외한 채로 나머지 모델만으로 결과 CSV 생성
"""

import os
import numpy as np
import pandas as pd
from netCDF4 import Dataset
import multiprocessing


def is_normal_value(arr):
    arr_mask = (~np.isnan(arr)) & (arr != 0)
    arr_str = arr.astype(str)
    arr_mask &= ~(np.char.find(arr_str, "+") >= 0)
    arr_mask &= ~(np.char.find(arr_str, "-") >= 0)
    arr_mask &= ~(arr_str == "nan")
    arr_mask &= ~(arr_str == "NaN")
    arr_mask &= ~(arr_str == "None")
    return arr_mask


# Haversine(단위: km)
def haversine(lon1, lat1, lon2, lat2):
    R = 6371.0
    lon1 = np.deg2rad(lon1)
    lat1 = np.deg2rad(lat1)
    lon2 = np.deg2rad(lon2)
    lat2 = np.deg2rad(lat2)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    c = 2 * np.arcsin(np.sqrt(a))
    return R * c


def get_time_length(ds, time_var):
    if time_var in ds.variables:
        return ds.variables[time_var].shape[0]
    elif time_var in ds.dimensions:
        return ds.dimensions[time_var].size
    else:
        for tv in ["ocean_time", "time", "tim"]:
            if tv in ds.variables:
                return ds.variables[tv].shape[0]
            elif tv in ds.dimensions:
                return ds.dimensions[tv].size
        raise KeyError(f"시간 변수({time_var}) 또는 차원 없음!")


def find_closest_valid_within_radius(lat_grid, lon_grid, valid_mask, pt, radius_km=40):
    # pt: [lon, lat]
    dist_km = haversine(lon_grid, lat_grid, pt[0], pt[1])
    candidate_mask = (dist_km <= radius_km) & valid_mask
    if not np.any(candidate_mask):
        return None
    dists = np.where(candidate_mask, dist_km, np.inf)
    idx = np.unravel_index(np.argmin(dists), dists.shape)
    if np.isinf(dists[idx]):
        return None
    return idx


def process_model(args):
    """
    반환:
      (model_name, df_or_none, error_msg_or_none)
    - 오류가 나면 df는 None, error_msg는 문자열
    - 정상 처리면 df는 결과 DataFrame, error_msg는 None
    """
    model_name, info, points_dict = args
    points = pd.DataFrame(points_dict)

    # 혹시 파일이 중간에 삭제/이동된 경우까지 방어
    if not os.path.exists(info["file"]):
        return (model_name, None, f"파일 없음: {info['file']}")

    try:
        ds = Dataset(info["file"], "r")
    except Exception as e:
        return (model_name, None, f"파일 열기 실패: {info['file']} | {type(e).__name__}: {e}")

    try:
        lat = ds.variables[info["lat"]][:]
        lon = ds.variables[info["lon"]][:]

        if lat.ndim == 1 and lon.ndim == 1:
            lat_grid, lon_grid = np.meshgrid(lat, lon, indexing="ij")
        elif lat.ndim == 2:
            lat_grid, lon_grid = lat, lon
        else:
            raise ValueError("격자 차원 해석 필요")

        var_name = info["var"]

        # WRF 계열은 time 검증 없이 최근접만
        if model_name in ["WRF", "WRFdm1", "WRFdm2"]:
            Xs, Ys = [], []
            for _, row in points.iterrows():
                pt = [row["lon"], row["lat"]]
                idx = find_closest_valid_within_radius(
                    lat_grid,
                    lon_grid,
                    np.ones(lat_grid.shape, dtype=bool),
                    pt,
                    radius_km=40,
                )
                if idx is None:
                    Xs.append(np.nan)
                    Ys.append(np.nan)
                else:
                    Xs.append(idx[1])
                    Ys.append(idx[0])

            points[f"{model_name}_X"] = Xs
            points[f"{model_name}_Y"] = Ys

        else:
            time_var = info["time"]
            time_len = get_time_length(ds, time_var)
            time_idxs = list(range(min(20, time_len)))

            var_arr = ds.variables[var_name][:]  # (time, y, x)
            if var_arr.ndim != 3:
                raise ValueError("변수 shape이 (time, y, x)가 아님!")

            valid_mask = np.ones(var_arr.shape[1:], dtype=bool)
            for t in time_idxs:
                valid_mask &= is_normal_value(var_arr[t])

            Xs, Ys, result_first_time = [], [], []
            for _, row in points.iterrows():
                pt = [row["lon"], row["lat"]]
                idx = find_closest_valid_within_radius(lat_grid, lon_grid, valid_mask, pt, radius_km=40)
                if idx is None:
                    Xs.append(np.nan)
                    Ys.append(np.nan)
                    result_first_time.append(np.nan)
                else:
                    Xs.append(idx[1])
                    Ys.append(idx[0])
                    result_first_time.append(var_arr[time_idxs[0], idx[0], idx[1]])

            points[f"{model_name}_X"] = Xs
            points[f"{model_name}_Y"] = Ys
            points[f"result_{model_name}"] = result_first_time

        keep_cols = [c for c in points.columns if c.startswith(model_name) or c == f"result_{model_name}"]
        keep_cols += ["lon", "lat"]

        return (model_name, points[keep_cols], None)

    except Exception as e:
        return (model_name, None, f"처리 실패: {info['file']} | {type(e).__name__}: {e}")

    finally:
        try:
            ds.close()
        except Exception:
            pass


if __name__ == "__main__":
    # ===== 경로 정책 =====
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    MODEL_DIR = os.path.join(BASE_DIR, "model")

    point_path = os.path.join(BASE_DIR, "포인트.xlsx")
    out_csv_path = os.path.join(BASE_DIR, "포인트지정.csv")

    # (선택) 누락/오류 로그 저장 파일
    missing_log_path = os.path.join(BASE_DIR, "missing_models.txt")

    # 입력 파일 존재 체크
    if not os.path.exists(point_path):
        raise FileNotFoundError(f"입력 파일이 없습니다: {point_path}")

    points = pd.read_excel(point_path)
    points_dict = points.to_dict(orient="list")

    # 모델 파일 정의
    model_infos = {
        "YES3K": {
            "file": os.path.join(MODEL_DIR, "YES3K_2023041900.nc"),
            "lat": "lat_rho",
            "lon": "lon_rho",
            "var": "temp",
            "time": "ocean_time",
        },
        "WW3": {
            "file": os.path.join(MODEL_DIR, "WW3_2023041800.nc"),
            "lat": "Latitude",
            "lon": "Longitude",
            "var": "Hsig",
            "time": "ocean_time",
        },
        "CWW3_1": {
            "file": os.path.join(MODEL_DIR, "CWW3_2023041800.nc"),
            "lat": "lat1_dajn",
            "lon": "lon1_dajn",
            "var": "hsig1_dajn",
            "time": "time",
        },
        "CWW3_2": {
            "file": os.path.join(MODEL_DIR, "CWW3_2023041800.nc"),
            "lat": "lat2_gwju",
            "lon": "lon2_gwju",
            "var": "hsig2_gwju",
            "time": "time",
        },
        "CWW3_3": {
            "file": os.path.join(MODEL_DIR, "CWW3_2023041800.nc"),
            "lat": "lat3_jeju",
            "lon": "lon3_jeju",
            "var": "hsig3_jeju",
            "time": "time",
        },
        "CWW3_4": {
            "file": os.path.join(MODEL_DIR, "CWW3_2023041800.nc"),
            "lat": "lat4_busn",
            "lon": "lon4_busn",
            "var": "hsig4_busn",
            "time": "time",
        },
        "CWW3_5": {
            "file": os.path.join(MODEL_DIR, "CWW3_2023041800.nc"),
            "lat": "lat5_gawn",
            "lon": "lon5_gawn",
            "var": "hsig5_gawn",
            "time": "time",
        },
        "RWW3": {
            "file": os.path.join(MODEL_DIR, "RWW3_2023041800.nc"),
            "lat": "lat1_dajn",
            "lon": "lon1_dajn",
            "var": "hsig1_dajn",
            "time": "time",
        },
        "MOHID2k": {
            "file": os.path.join(MODEL_DIR, "Mohid2k_2023041712.nc"),
            "lat": "lat_rho",
            "lon": "lon_rho",
            "var": "temp",
            "time": "ocean_time",
        },
        "MOHID300": {
            "file": os.path.join(MODEL_DIR, "L4_OC_2022122912.nc"),
            "lat": "lat",
            "lon": "lon",
            "var": "temp",
            "time": "ocean_time",
        },
        "SWAN2k": {
            "file": os.path.join(MODEL_DIR, "L2_WA_2024050112.nc"),
            "lat": "lat",
            "lon": "lon",
            "var": "Hs",
            "time": "tim",
        },
        "SWAN300": {
            "file": os.path.join(MODEL_DIR, "L4_WA_2022123012.nc"),
            "lat": "lat",
            "lon": "lon",
            "var": "Hs",
            "time": "tim",
        },
        "WRF": {
            "file": os.path.join(MODEL_DIR, "WRF_20230427.nc"),
            "lat": "lat_rho",
            "lon": "lon_rho",
            "var": "Tair",
        },
        "WRFdm1": {
            "file": os.path.join(MODEL_DIR, "wrfdm1_20240627_20240706.nc"),
            "lat": "lat_rho",
            "lon": "lon_rho",
            "var": "Tair",
        },
        "WRFdm2": {
            "file": os.path.join(MODEL_DIR, "wrfdm2_20240627_20240706.nc"),
            "lat": "lat_rho",
            "lon": "lon_rho",
            "var": "Tair",
        },
    }

    # ===== 1) 파일 존재 여부로 1차 필터링 (없는 모델은 제외) =====
    available_model_infos = {}
    missing_models = []

    for model_name, info in model_infos.items():
        if os.path.exists(info["file"]):
            available_model_infos[model_name] = info
        else:
            missing_models.append((model_name, info["file"], "파일 없음"))

    # ===== 2) 멀티프로세싱 실행 (열기/처리 실패 모델도 제외) =====
    args_list = [(model_name, info, points_dict) for model_name, info in available_model_infos.items()]

    results = []
    if args_list:
        with multiprocessing.Pool(processes=min(len(args_list), multiprocessing.cpu_count())) as pool:
            results = list(pool.imap(process_model, args_list))

    # ===== 3) 결과 취합 + 실패 모델 수집 =====
    final = points.copy()
    failed_models = []  # (model_name, file, reason)

    for model_name, df, err in results:
        info = available_model_infos.get(model_name, {})
        file_path = info.get("file", "")

        if err is not None or df is None:
            failed_models.append((model_name, file_path, err or "알 수 없는 오류"))
            continue

        to_add = df[[col for col in df.columns if col not in ["lon", "lat"]]]
        final = pd.concat([final, to_add], axis=1)

    # ===== 4) 누락/실패 모델 안내 (출력 + 파일로 저장) =====
    all_missing = missing_models + failed_models

    if all_missing:
        lines = []
        lines.append("다음 모델은 제외하고 결과를 생성했습니다.")
        for m, f, reason in all_missing:
            lines.append(f"- {m}: {f} ({reason})")

        # 콘솔 출력(콘솔이 있는 환경에서만 보임)
        print("⚠️ " + lines[0])
        for line in lines[1:]:
            print("   " + line)

#        # 파일 저장(배포/무콘솔 환경에서도 확인 가능)
#        try:
#            with open(missing_log_path, "w", encoding="utf-8") as fp:
#                fp.write("\n".join(lines) + "\n")
#        except Exception:
#            # 로그 저장 실패는 본 작업을 막지 않음
#            pass

    # ===== 5) 결과 저장 =====
    final.to_csv(out_csv_path, encoding="euc-kr", index=False)
