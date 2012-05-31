'Network model using a modified kruskal algorithm'
# Import system modules
import os
import functools
import numpy as np
from pysal.cg import Arc_KDTree
from scipy.spatial import KDTree
# Import custom modules
from np.lib import network, store, geometry_store, variable_store


class MinimumNodeCountPerSubnetwork(variable_store.Variable):

    section = 'algorithm'
    option = 'minimum node count per subnetwork'
    c = dict(parse=int)
    default = 2
    units = 'nodes'


class MaximumNearestNeighborCount(variable_store.Variable):

    section = 'algorithm'
    option = 'maximum nearest neighbor count'
    c = dict(parse=int)
    default = 5
    units = 'nodes'


class ExistingNetworks(variable_store.Variable):

    section = 'network'
    option = 'existing networks'
    c = dict(parse=str, input=variable_store.inputFile)
    default = ''


class VariableStore(variable_store.VariableStore):

    variableClasses = [
        MinimumNodeCountPerSubnetwork,
        MaximumNearestNeighborCount,
        ExistingNetworks,
    ]

    def buildNetworkFromNodes(self, nodes, proj4):
        'Build a network using the given nodes'
        # If the spatial reference has units in meters,
        if '+units=m' in proj4:
            # Use euclidean distance
            computeDistance = network.computeEuclideanDistance
        else:
            # Use spherical distance
            computeDistance = network.computeSphericalDistance
        # Run algorithm given nodes
        net = self.buildNetworkFromSegments(*self.generateSegments(nodes, computeDistance, proj4))
        # Eliminate subnetworks that have too few real nodes
        minimumNodeCountPerSubnetwork = self.get(MinimumNodeCountPerSubnetwork)
        subnets = []
        for subnet in net.cycleSubnets():
            if subnet.countNodes() >= minimumNodeCountPerSubnetwork:
                subnets.append(subnet)
        net.subnets = subnets
        # Return
        return net

    def generateSegments(self, nodes, computeDistance, proj4):
        'Generate segment candidates connecting nodes to the existing grid'
        # Prepare
        segments = []
        segmentFactory = network.SegmentFactory(nodes, computeDistance, proj4)
        networkNodes = segmentFactory.getNodes()
        net = network.Network(segmentFactory)
        networkRelativePath = self.get(ExistingNetworks)
        # If we have existing networks,
        if networkRelativePath:
            # Reconstruct path
            networkArchivePath = os.path.join(self.state[0].getBasePath(), networkRelativePath)
            if not os.path.exists(networkArchivePath):
                raise variable_store.VariableError('Expected ZIP archive containing shapefile for existing networks')
            isValid, networkPath = store.unzip(networkArchivePath, 'shp')
            if not isValid:
                raise variable_store.VariableError('Could not find shapefile in ZIP archive for existing networks')
            # Load network
            networkProj4, networkGeometries = geometry_store.load(networkPath)[:2]
            networkCoordinatePairs = network.yieldSimplifiedCoordinatePairs(networkGeometries)
            # Prepare
            transform_point = geometry_store.get_transform_point(networkProj4, proj4)
            # Load existing network as a single subnet and allow overlapping segments
            net.subnets.append(network.Subnet([segmentFactory.getSegment(transform_point(c1[0], c1[1]), transform_point(c2[0], c2[1]), is_existing=True) for c1, c2 in networkCoordinatePairs]))
            # Add candidate segments that connect each node to its projection on the existing network
            segments.extend(net.project(networkNodes))
        # Prepare matrix where the rows are nodes and the columns are node coordinates
        networkNodeMatrix = np.array([node.getCoordinates() for node in networkNodes])
        # Define get_nearestNeighbors()
        if computeDistance == network.computeEuclideanDistance:
            kdTree = KDTree(networkNodeMatrix)
        else:
            earthRadiusInMeters = 6371010
            kdTree = Arc_KDTree(networkNodeMatrix, radius=earthRadiusInMeters)
        get_nearestNeighbors = functools.partial(kdTree.query, k=self.get(MaximumNearestNeighborCount))
        # For each node,
        for node1 in networkNodes:
            # Get its nearest neighbors
            nodeIndices = get_nearestNeighbors(node1.getCoordinates())[1]
            # Add candidate segments from the node to each of its nearest neighbors
            for node2Coordinates in networkNodeMatrix[nodeIndices]:
                # Let getSegment() compute segment weight in case we want to customize how we weight each segment
                segments.append(segmentFactory.getSegment(node1.getCoordinates(), tuple(node2Coordinates)))
        # Return
        return segments, net 

    def buildNetworkFromSegments(self, segments, net):
        """
        MAKE SURE THAT SEGMENTS WITH IDENTICAL COORDINATES CORRESPOND TO THE SAME OBJECT
        MAKE SURE THAT NODES WITH IDENTICAL COORDINATES CORRESPOND TO THE SAME OBJECT
        OTHERWISE WEIGHTS WILL NOT UPDATE
        """
        print 'Building network from segments...'
        # Cycle segments starting with the smallest first
        for segment in sorted(segments, key=lambda x: x.getWeight()):
            # Prepare
            node1, node2 = segment.getNodes()
            # Prepare
            n1Weight, n2Weight, sWeight = node1.getWeight(), node2.getWeight(), segment.getWeight()
            node1Qualifies = n1Weight >= sWeight or node1.getID() < 0 # canAfford or isFake
            node2Qualifies = n2Weight >= sWeight or node2.getID() < 0 # canAfford or isFake
            # If the segment qualifies,
            if node1Qualifies and node2Qualifies:
                # Try to add the segment
                subnet = net.addSegment(segment)
                # If the segment was added,
                if subnet:
                    weight = n1Weight + n2Weight - sWeight
                    for node in subnet.cycleNodes():
                        node.setWeight(weight)
        # Return
        return net


roots = [
    ExistingNetworks,
    MinimumNodeCountPerSubnetwork,
]
sections = [
    'network',
    'algorithm',
]
