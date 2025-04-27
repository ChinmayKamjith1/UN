"""
SafeNav Flask App
-----------------
- /report_incident: logs lat/lng to INCIDENT_CSV
- /get_route: geocodes start/end, avoids unsafe zones, returns car+walk data
Requirements: Flask, openrouteservice, shapely, pyproj
"""
from flask import Flask, request, jsonify, render_template
import openrouteservice
from openrouteservice import convert
from shapely.geometry import Polygon, mapping, MultiPolygon, Point
from pyproj import Transformer
from datetime import datetime
import os
import re

# creates web application and ORS client
app = Flask(__name__)
client = openrouteservice.Client(
    key=os.environ.get(
        '5b3ce3597851110001cf62486d3b180011c7482a984347bdbf8144b7',
        '5b3ce3597851110001cf62486d3b180011c7482a984347bdbf8144b7'
    )
)

# listen in for POST and CSV creation
@app.route('/report_incident', methods=['POST'])
def report_incident():
    data = request.json or {}
    lat = data.get('lat')
    lng = data.get('lng')
    if lat is None or lng is None:
        return jsonify({'error': 'Missing coordinates'}), 400

    if not os.path.exists('incidents.csv'):
        with open('incidents.csv', 'w') as f:
            f.write('lat,lng,timestamp\n')

    with open('incidents.csv', 'a') as f:
        f.write(f"{lat},{lng},{datetime.utcnow().isoformat()}\n")

    return jsonify({'status': 'ok'}), 200

# return map_interface
@app.route('/')
def index():
    return render_template('map_interface.html')

# get both addresses
@app.route('/get_route', methods=['POST'])
def get_route():
    data = request.json or {}
    raw_start = data.get('start', '').strip()
    end = data.get('end', '').strip()

    if not raw_start or not end:
        return jsonify({'error': 'Both addresses are required.'}), 400

    # if start is "lat,lng", parse directly; otherwise geocode text
    coord_pattern = re.compile(r'^\s*(-?\d+(\.\d+)?),\s*(-?\d+(\.\d+)?)\s*$')
    m = coord_pattern.match(raw_start)
    if m:
        lat, lng = float(m.group(1)), float(m.group(3))
        sc = (lng, lat)
    else:
        try:
            sf = client.pelias_search(text=raw_start)['features']
        except Exception as e:
            return jsonify({'error': 'Geocoding failed: ' + str(e)}), 400
        if not sf:
            return jsonify({'error': 'Could not geocode start address.'}), 400
        sc = sf[0]['geometry']['coordinates']

    # always geocode end address because there is no "use current address" unlike start address
    try:
        ef = client.pelias_search(text=end)['features']
    except Exception as e:
        return jsonify({'error': 'Geocoding failed: ' + str(e)}), 400
    if not ef:
        return jsonify({'error': 'Could not geocode end address.'}), 400
    ec = ef[0]['geometry']['coordinates']

    coords = (tuple(sc), tuple(ec))

    # Fake unsafe zones for test
    unsafe = [
        (-117.8265, 33.6846), (-117.8100, 33.7036), (-117.8500, 33.6700),
        (-118.2437, 34.0522), (-118.2890, 34.0211), (-118.4108, 34.0194),
        (-118.2817, 34.1400), (-118.4662, 34.0525), (-118.1570, 34.0686),
        (-118.3520, 34.1016), (-117.6678, 33.6391), (-117.9145, 33.8353),
        (-117.7513, 33.5539), (-117.8180, 33.6741), (-118.0000, 33.8166),
        (-117.9931, 33.7175), (-117.6981, 33.6681), (-118.1553, 33.7866),
        (-118.0368, 34.1458), (-118.0845, 34.1480), (-118.2000, 34.0407),
        (-117.9143, 33.7879)
    ]

    # transform geographical coordinates to projected UTM coordinates
    def buf(pt, r=200):
        tr = Transformer.from_crs('epsg:4326', 'epsg:32611', always_xy=True)
        x, y = tr.transform(*pt)
        b = Point(x, y).buffer(r)
        poly = []

        # Shapely makes a list of vertices in exterior of buffered circle and build Polygon
        for bx, by in b.exterior.coords:
            lon, lat = tr.transform(bx, by, direction='INVERSE')
            poly.append((lon, lat))
        return Polygon(poly)

    # list of these circles
    polys = [buf(u) for u in unsafe]

    # ask ORS for driving route with avoidance; revert back to plain driving routing if avoidance fails
    try:
        dr = client.directions(
            coords,
            profile='driving-car',
            format='json',
            options={'avoid_polygons': mapping(MultiPolygon(polys))}
        )
    except openrouteservice.exceptions.ApiError as e:
        msg = None
        if e.args and isinstance(e.args[0], dict):
            msg = e.args[0].get('error', {}).get('message')
        if not msg:
            msg = str(e)
        try:
            dr = client.directions(coords, profile='driving-car', format='json')
        except Exception:
            return jsonify({'error': msg}), 400
    except Exception as e:
        return jsonify({'error': 'Unexpected: ' + str(e)}), 500

    routes = dr.get('routes') or []
    if not routes:
        return jsonify({'error': 'No driving route found.'}), 400

    # call to ORS for walking route and display HTTP 500 if there is an error
    try:
        wr = client.directions(coords, profile='foot-walking', format='json')
    except Exception as e:
        return jsonify({'error': 'Walking route failed: ' + str(e)}), 500

    wts = wr.get('routes') or []
    if not wts:
        return jsonify({'error': 'No walking route found.'}), 400

    # pull distance and duration for both driving and walking
    car_sum = routes[0]['summary']
    walk_sum = wts[0]['summary']

    # decode polyline as array of [lat,lon] for frontend to plot
    geom = routes[0]['geometry']
    dec = convert.decode_polyline(geom)
    route_coords = [(lat, lon) for lon, lat in dec['coordinates']]

    # package into one JSON
    return jsonify({
        'start_coords': sc,
        'end_coords': ec,
        'route_coords': route_coords,
        'dist_car': car_sum['distance'],
        'dur_car': car_sum['duration'],
        'dist_walk': walk_sum['distance'],
        'dur_walk': walk_sum['duration']
    })


# run app
if __name__ == '__main__':
    app.run(debug=True)
